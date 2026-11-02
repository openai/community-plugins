#!/usr/bin/env node
// Actual official MCP SDK -> bundled stdio server -> isolated native OpenPnP simulator.
// Short runs exercise this harness; they never qualify an eight-hour soak or hardware.
import { createHash, randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { lstat, mkdir, open, readFile, readdir, rename, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { setTimeout as delay } from 'node:timers/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { Client } from '../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { BridgeClient } from '../src/openpnp/node/client.mjs';
import { readCompleteResponse } from './openpnp-read-response.mjs';
import { SoakTimingGuard, TIMING_POLICY } from './openpnp-soak-timing.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const PINNED_UPSTREAM = '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c';
const TERMINAL = new Set(['succeeded', 'failed', 'aborted', 'cancelled', 'outcome_unknown', 'unknown']);
const REQUIRED_TOOLS = ['get_capabilities', 'get_status', 'get_configuration', 'request_control_session', 'get_request_status',
  'renew_control_session', 'release_control_session', 'set_machine_enabled', 'home_machine', 'plan_motion', 'execute_motion',
  'capture_camera', 'get_native_artifact', 'get_operation', 'get_events', 'import_job', 'get_artifact',
  'validate_imported_job', 'prepare_job', 'validate_job', 'start_job', 'pause_job', 'read_response_page'];
const METRIC_KEYS = ['uptime_ms', 'heap_used_bytes', 'heap_committed_bytes', 'heap_max_bytes', 'event_buffer_count',
  'operation_count', 'request_count', 'plan_count', 'artifact_count', 'artifact_metadata_cache_count',
  'artifact_bytes', 'artifact_memory_content_bytes', 'journal_bytes'];
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const execFileAsync = promisify(execFile);
const fail = (code, message) => { throw Object.assign(new Error(message), { code }); };
const ensure = (condition, message) => { if (!condition) fail('SOAK_PRECONDITION', message); };

export function parseOptions(argv) {
  const result = { durationHours: 8, placements: 10000 };
  const names = { '--connection-file': 'connectionFile', '--evidence-dir': 'evidenceDir', '--duration-hours': 'durationHours',
    '--placements': 'placements', '--native-build-manifest': 'nativeBuildManifest', '--native-pid': 'nativePid', '--event-poll-seconds': 'eventPollSeconds' };
  const seen = new Set();
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (arg === '--help') { result.help = true; continue; }
    ensure(names[arg] && !seen.has(arg), `Unknown or repeated option: ${arg}`);
    ensure(argv[index + 1] && !argv[index + 1].startsWith('--'), `Missing value for ${arg}`);
    seen.add(arg); result[names[arg]] = argv[++index];
  }
  if (result.help) return result;
  result.durationHours = Number(result.durationHours); result.placements = Number(result.placements);
  ensure(Number.isFinite(result.durationHours) && result.durationHours > 0 && result.durationHours <= 24, 'Duration must be greater than zero and at most 24 hours.');
  ensure(Number.isInteger(result.placements) && result.placements > 0 && result.placements <= 10000, 'Placements must be an integer from 1 to 10000.');
  for (const key of ['connectionFile', 'evidenceDir']) ensure(typeof result[key] === 'string' && path.isAbsolute(result[key]), `${key} must be an absolute path.`);
  if (result.nativeBuildManifest) ensure(path.isAbsolute(result.nativeBuildManifest), 'nativeBuildManifest must be an absolute path.');
  if (result.nativePid !== undefined) { result.nativePid = Number(result.nativePid); ensure(Number.isSafeInteger(result.nativePid) && result.nativePid > 0, 'nativePid must be a positive integer for the local JVM.'); }
  if (result.eventPollSeconds !== undefined) { result.eventPollSeconds = Number(result.eventPollSeconds); ensure(Number.isSafeInteger(result.eventPollSeconds) && result.eventPollSeconds >= 60 && result.eventPollSeconds <= 600, 'eventPollSeconds must be an integer from 60 to 600.'); }
  result.smoke = result.durationHours < 8 || result.placements !== 10000;
  return result;
}

// Event delivery is deliberately slow; status and lease supervision retain their independent cadence.
export function eventPollDue(nowMs, lastEventMs, pollSeconds = 60, force = false) {
  return force || nowMs - lastEventMs >= pollSeconds * 1000;
}

export function inventoryFor(machine, partId) {
  ensure(Array.isArray(machine?.feeders), 'Native feeder inventory is missing.');
  const feeders = machine.feeders.filter(feeder => feeder.enabled && feeder.part_id === partId);
  ensure(feeders.length > 0, `No enabled native supply for ${partId}.`);
  for (const feeder of feeders) {
    ensure(typeof feeder.id === 'string' && feeder.class === 'org.openpnp.machine.reference.feeder.ReferenceTrayFeeder' && feeder.virtual_supply === true,
      'Every enabled feeder for the selected part must be an attested native virtual tray.');
    ensure(Number.isSafeInteger(feeder.capacity) && feeder.capacity > 0 && Number.isSafeInteger(feeder.feed_count) && feeder.feed_count >= 0 && feeder.feed_count <= feeder.capacity,
      'Finite native feeder capacity/count is missing or invalid.');
  }
  const capacity = feeders.reduce((sum, feeder) => sum + feeder.capacity, 0);
  const feedCount = feeders.reduce((sum, feeder) => sum + feeder.feed_count, 0);
  return { feeders: feeders.map(({ id, capacity, feed_count }) => ({ id, capacity, feed_count })), capacity, feed_count: feedCount, remaining: capacity - feedCount };
}

export function checkMetrics(metrics, limits, { requireQualificationBounds = false } = {}) {
  for (const key of METRIC_KEYS) ensure(Number.isSafeInteger(metrics?.[key]) && metrics[key] >= 0, `Missing or invalid native metric: ${key}`);
  ensure(metrics.heap_used_bytes <= metrics.heap_committed_bytes && metrics.heap_committed_bytes <= metrics.heap_max_bytes && metrics.heap_max_bytes > 0, 'Native heap metrics are inconsistent.');
  for (const [metric, cap] of Object.entries({ event_buffer_count: 5000, plan_count: limits.plans,
    artifact_metadata_cache_count: limits.artifact_metadata_cache, request_count: limits.retained_requests, journal_bytes: limits.journal_bytes })) {
    ensure(Number.isSafeInteger(cap) && cap > 0 && metrics[metric] <= cap, `${metric} exceeds its native retention bound or the bound is absent.`);
  }
  if (requireQualificationBounds || limits.artifact_storage_bytes !== undefined) {
    ensure(Number.isSafeInteger(limits.artifact_storage_bytes) && limits.artifact_storage_bytes > 0 && metrics.artifact_bytes <= limits.artifact_storage_bytes,
      'artifact_bytes exceeds its native aggregate storage bound or the bound is absent.');
  }
  if (requireQualificationBounds) ensure(metrics.heap_max_bytes <= 2 * 1024 ** 3, 'A full soak requires the native JVM maximum heap to be at most 2 GiB.');
  ensure(metrics.artifact_memory_content_bytes === 0, 'Native artifact payloads unexpectedly remain cached in memory.');
  return metrics;
}

export async function waitForNativeIdle(readStatus, { required = true, timeoutMs = 30000, pollIntervalMs = 100, supervise = async () => {} } = {}) {
  const began = performance.now();
  for (;;) {
    const status = await readStatus();
    ensure(typeof status.native_busy === 'boolean' || (!required && status.native_busy === undefined), 'Native executor busy observation is absent or invalid.');
    if (status.native_busy !== true && status.active_operation_id == null) return status;
    if (performance.now() - began >= timeoutMs) fail('NATIVE_IDLE_TIMEOUT', 'Native executor did not become idle after the completed operation; no command was replayed.');
    await supervise(); await delay(pollIntervalMs);
  }
}

export function validatePrerequisites(capabilities, status, placements, metricOptions = {}) {
  const native = capabilities?.bridge;
  ensure(capabilities?.connected === true && native?.upstream_commit === PINNED_UPSTREAM && native.bridge_version === '0.1.0' && native.protocol_version === '1.0' && native.schema_version === 1,
    'A connected, compatible pinned native OpenPnP build is required.');
  ensure(native.simulation === true && native.hardware_qualified === false && native.simulator_profile === 'sustained-workload' &&
    native.simulator_attestation?.profile === 'sustained-workload' && native.simulator_attestation.fixture_provenance === 'native-finite-tray-supply-zero-pitch' &&
    native.simulator_attestation.driver_class === 'org.openpnp.machine.reference.driver.NullDriver' && native.simulator_attestation.checked_at_dispatch === true,
  'Only the attested sustained-workload native simulator is allowed.');
  if (metricOptions.requireQualificationBounds) ensure(native.native_job_order === 'Unsorted' && native.simulator_attestation.native_job_order === 'Unsorted',
    'A full soak requires the sustained fixture to advertise actual native Unsorted job order. Older or NozzleTips fixtures cannot qualify this recipe.');
  for (const tool of REQUIRED_TOOLS) ensure(capabilities.tools?.includes(`openpnp_${tool}`), `Required MCP capability is missing: openpnp_${tool}`);
  ensure(typeof native.machine_id === 'string' && typeof native.bridge_instance_id === 'string' && native.machine_id === status?.machine_id && native.bridge_instance_id === status.bridge_instance_id,
    'Initial machine/bridge identities do not match.');
  ensure(status.job_state === 'absent' && status.job_id == null && status.active_operation_id == null, 'A fresh simulator with no loaded job or active operation is required.');
  if (metricOptions.requireQualificationBounds) ensure(status.native_busy === false, 'A full soak requires an observed idle native executor.');
  ensure(status.configuration_fault === false && status.journal_fault === false, 'Native fault fences are active or unavailable.');
  ensure(native.limits?.canonical_placements >= placements, 'The native canonical placement limit is too small.');
  ensure(Array.isArray(status.machine?.drivers) && status.machine.drivers.length > 0 && status.machine.drivers.every(driver => driver.class === 'org.openpnp.machine.reference.driver.NullDriver'), 'Only actual native NullDriver instances are allowed.');
  const part = status.machine.parts?.find(item => item.id === 'R0603-1K');
  ensure(part?.package_id === 'R0603' && Number.isFinite(part.height_mm) && part.height_mm > 0, 'The native R0603-1K/R0603 part and its actual height are required.');
  if (status.response_view?.name === 'progress') ensure(status.machine?.feeders_complete === true, 'Progress feeder inventory is incomplete; this workload requires the complete selected supply.');
    const inventory = inventoryFor(status.machine, part.id);
  ensure(inventory.feed_count === 0 && inventory.capacity >= placements, 'The selected finite supply must be unconsumed and sufficient; this runner never resets feeders.');
  if (placements === 10000) ensure(inventory.capacity === placements, 'A full run requires exactly 10000 available native slots across the selected part supply so remaining stock can reach zero.');
  checkMetrics(status.metrics, native.limits, metricOptions);
  return { part, inventory };
}

export function makeWorkloadCsv(part, placements) {
  ensure(part?.id === 'R0603-1K' && part.package_id === 'R0603' && Number.isFinite(part.height_mm) && part.height_mm > 0, 'Use the observed native part/package/height.');
  ensure(Number.isInteger(placements) && placements >= 1 && placements <= 10000, 'CSV workload count must be 1..10000.');
  const rows = ['Ref,Val,Package,PosX,PosY,Rot,Side,PartId,Height'];
  for (let i = 0; i < placements; i++) rows.push(`R${i + 1},1K,${part.package_id},${((i % 100) + 0.5) * 1.5},${(Math.floor(i / 100) + 0.5) * 1.5},0,top,${part.id},${part.height_mm}`);
  return rows.join('\n') + '\n';
}

export function verifyNativeBuildBinding(native, packaged, { required = false } = {}) {
  if (native.bridge_artifact_hash_available !== true) {
    ensure(!required, 'A full soak requires the actual loaded native bridge JAR digest. Rebuild and start the stabilized packaged bridge.');
    return { verified: false, reason: 'Loaded-JAR hash unavailable in this smoke build.' };
  }
  ensure(/^[a-f0-9]{64}$/.test(native.bridge_artifact_sha256) && native.bridge_artifact_sha256 === packaged.bridge_sha256,
    'The serving native bridge JAR digest does not match the selected local package.');
  return { verified: true, sha256: native.bridge_artifact_sha256, provenance: 'Native Bridge.class code-source JAR digest matched to the locally verified packaged JAR.' };
}

async function readMcpFiles(directory) {
  const files = []; let totalBytes = 0, directories = 0;
  async function visit(relative = '', depth = 0) {
    ensure(depth <= 8 && ++directories <= 256 && (await lstat(path.join(directory, relative))).isDirectory(), 'MCP snapshot must contain bounded real directories.');
    const entries = await readdir(path.join(directory, relative), { withFileTypes: true });
    entries.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
    for (const entry of entries) {
      const name = relative ? `${relative}/${entry.name}` : entry.name;
      if (entry.isDirectory()) { await visit(name, depth + 1); continue; }
      ensure(entry.isFile() && files.length < 256, 'MCP snapshot rejects links, special files and more than 256 files.');
      const handle = await open(path.join(directory, name), constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
      let bytes;
      try {
        const info = await handle.stat();
        ensure(info.isFile() && info.size <= 8 * 1024 ** 2, 'MCP snapshot file is not a bounded regular file.');
        bytes = await handle.readFile();
        const after = await handle.stat();
        ensure(bytes.length === info.size && after.size === info.size && after.mtimeMs === info.mtimeMs, 'MCP file changed during snapshot read.');
      } finally { await handle.close(); }
      totalBytes += bytes.length; ensure(totalBytes <= 32 * 1024 ** 2, 'MCP snapshot exceeds 32 MiB.');
      files.push({ path: name, bytes: bytes.length, sha256: hash(bytes), content: bytes });
    }
  }
  await visit();
  ensure(files.some(file => file.path === 'server.mjs'), 'MCP snapshot has no server.mjs entrypoint.');
  return files;
}

function mcpManifest(files) {
  const entries = files.map(({ content, ...entry }) => entry);
  return { files: entries, tree_sha256: hash(JSON.stringify(entries)), server_sha256: entries.find(entry => entry.path === 'server.mjs').sha256 };
}

export async function verifyMcpSnapshot(directory, expected) {
  const actual = mcpManifest(await readMcpFiles(directory));
  ensure(actual.tree_sha256 === expected.tree_sha256 && JSON.stringify(actual.files) === JSON.stringify(expected.files), 'The isolated MCP snapshot changed after creation.');
  return actual;
}

export async function snapshotMcpDirectory(source, destination) {
  const files = await readMcpFiles(source);
  await mkdir(destination, { mode: 0o700 }); // Exclusive: never merge with another run or an existing directory.
  for (const file of files) {
    await mkdir(path.dirname(path.join(destination, file.path)), { recursive: true, mode: 0o700 });
    const handle = await open(path.join(destination, file.path), 'wx', 0o400);
    try { await handle.writeFile(file.content); await handle.sync(); } finally { await handle.close(); }
  }
  const expected = mcpManifest(files);
  const verified = await verifyMcpSnapshot(destination, expected);
  return { schema_version: 1, ...verified,
    provenance: 'All bundled MCP files copied to this run before launch; actual copied bytes hashed and reread. Workspace rebuilds cannot change this copy.' };
}

// Unit-testable transport policy only. The executable runner always supplies the real SDK client.
// Resolve admission by the original ID; never resend the effect, including when the lookup fails.
export async function dispatchMutationOnce(call, name, args, onUnknown = async () => {}) {
  ensure(typeof args.request_id === 'string' && args.request_id, 'A durable request identity is required.');
  try { return await call(name, args); }
  catch (error) {
    if (error.code && error.code !== 'OUTCOME_UNKNOWN' && error.code !== 'MCP_TRANSPORT_ERROR') throw error;
    await onUnknown({ tool: name, request_id: args.request_id, outcome: 'unknown', message: 'Response missing; reading original request status once, without resending.' });
    let known;
    try { known = await call('get_request_status', { request_id: args.request_id }); }
    catch { fail('OUTCOME_UNKNOWN', `The outcome of ${name} (${args.request_id}) could not be observed. No retry was made.`); }
    const receipt = name === 'request_control_session' ? known?.session_receipt : known?.operation;
    if (known?.found !== true || !record(receipt)) fail('OUTCOME_UNKNOWN', `The outcome of ${name} (${args.request_id}) remains unknown. No retry was made.`);
    if (name === 'request_control_session' && (receipt.state !== 'succeeded' || typeof receipt.session_id !== 'string')) fail('OUTCOME_UNKNOWN', 'The ownership grant is not known to have completed.');
    return receipt;
  }
}

export class SampleSummary {
  constructor() { this.count = 0; this.fields = {}; }
  add(sample) {
    this.count++;
    for (const [key, value] of Object.entries(sample)) if (Number.isFinite(value)) {
      const item = this.fields[key] ??= { first: value, last: value, min: value, max: value, sum: 0 };
      item.last = value; item.min = Math.min(item.min, value); item.max = Math.max(item.max, value); item.sum += value;
    }
  }
  snapshot() { return { samples: this.count, fields: Object.fromEntries(Object.entries(this.fields).map(([key, value]) => [key, { ...value, mean: value.sum / this.count, net_change: value.last - value.first }])) }; }
}

export function parseNativeProcess(output) {
  const match = /^\s*(\d+)\s+([A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})\s+(.+?)\s*$/.exec(output);
  ensure(match && path.basename(match[3]) === 'java', 'The supplied PID did not identify a local java process with RSS and start-time data.');
  const rssBytes = Number(match[1]) * 1024; ensure(Number.isSafeInteger(rssBytes) && rssBytes > 0, 'Invalid JVM RSS measurement.');
  return { rss_bytes: rssBytes, started_at_ps: match[2], executable: path.basename(match[3]) };
}

export function latencyStats(values) {
  ensure(values.length > 0 && values.every(value => Number.isFinite(value) && value >= 0), 'Latency samples must be finite nonnegative values.');
  const sorted = [...values].sort((a, b) => a - b);
  return { samples: sorted.length, min_ms: sorted[0], peak_ms: sorted.at(-1), p95_ms: sorted[Math.ceil(sorted.length * 0.95) - 1],
    median_ms: sorted[Math.ceil(sorted.length * 0.5) - 1], mean_ms: sorted.reduce((sum, value) => sum + value, 0) / sorted.length };
}

export function qualifyResult({ smoke, elapsedMs, placements, completed, inventory, resyncCount, ambientCycles, failure, interrupted, rssMeasured = false, baselineMeasured = false }) {
  const checks = { eight_hours: elapsedMs >= 8 * 3600000, largest_advertised_placement_count: placements === 10000,
    native_job_completed: completed?.state === 'succeeded' && completed.result?.requested === placements && completed.result?.placed === placements,
    exact_native_feed_count: inventory?.feed_count === placements, finite_selected_supply_exhausted: inventory?.remaining === 0,
    event_overflow_resynchronized: resyncCount > 0, post_job_camera_motion_cycles: ambientCycles > 0,
    no_failure_or_interruption: !failure && !interrupted };
  return { simulator_soak_complete: !smoke && Object.values(checks).every(Boolean), checks, full_A11_qualified: false, hardware_qualified: false,
    independent_placement_inspection_count: 0, scope: smoke ? 'explicit-short-or-reduced-count-smoke-unqualified' : 'native-simulator-soak-only',
    soft_skips: [
      ...(!baselineMeasured ? [{ criterion: 'direct-native-versus-MCP-overhead-baseline', reason: 'No paired direct-native/MCP read baseline completed.' }] : []),
      { criterion: 'placement-cycle-overhead-baseline', reason: 'Paired get_status latency does not measure native-versus-MCP placement-cycle overhead; that controlled experiment is separate.' },
      ...(!rssMeasured ? [{ criterion: 'native-process-RSS', reason: 'No explicit local JVM PID was supplied and observed.' }] : []),
      { criterion: 'native-image-allocation-attribution', reason: 'Java heap and optional total JVM RSS are measured. Native image allocations are not independently attributed.' },
      { criterion: 'retention-steady-state-and-offline-failure-replay', reason: 'This run measures growth and enforces declared caps; retained journal/artifacts are preserved, not purged to manufacture steady state. Offline failure replay is a separate test.' },
      { criterion: 'hardware-quality-safety-and-maximum-panel-depth', reason: 'The fixture is virtual finite supply and one 150 mm board. Physical inspection and maximum panel depth are separate qualification work.' },
    ] };
}

class DurableEvidence {
  constructor(directory, file) { this.directory = directory; this.file = file; this.sequence = 0; }
  static async create(directory) {
    await mkdir(directory, { recursive: true, mode: 0o700 });
    if ((await readdir(directory)).length !== 0) fail('EEXIST', 'The evidence directory must be empty; prior artifacts will not be overwritten.');
    // Never append a new run into an old run, even when a prior process died before writing report.json.
    return new DurableEvidence(directory, await open(path.join(directory, 'progress.jsonl'), 'wx', 0o600));
  }
  async append(type, value) {
    await this.file.writeFile(JSON.stringify({ sequence: ++this.sequence, observed_at: new Date().toISOString(), type, ...value }) + '\n');
    await this.file.sync();
  }
  async save(name, value) {
    const destination = path.join(this.directory, name), temporary = `${destination}.${randomUUID()}.tmp`;
    const handle = await open(temporary, 'wx', 0o600);
    try { await handle.writeFile(typeof value === 'string' ? value : JSON.stringify(value, null, 2) + '\n'); await handle.sync(); } finally { await handle.close(); }
    await rename(temporary, destination);
    const dir = await open(this.directory, 'r'); try { await dir.sync(); } finally { await dir.close(); }
  }
  async close() { await this.file.close(); }
}

class Latencies {
  constructor() { this.tools = {}; this.bounds = [1, 5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000, 20000, 40000, Infinity]; }
  add(tool, ms, failed) {
    const item = this.tools[tool] ??= { count: 0, failures: 0, sum_ms: 0, min_ms: ms, max_ms: ms, histogram: this.bounds.map(upper => ({ upper_ms: Number.isFinite(upper) ? upper : null, count: 0 })) };
    item.count++; item.failures += Number(failed); item.sum_ms += ms; item.min_ms = Math.min(item.min_ms, ms); item.max_ms = Math.max(item.max_ms, ms);
    item.histogram[this.bounds.findIndex(upper => ms <= upper)].count++;
  }
  snapshot() {
    return Object.fromEntries(Object.entries(this.tools).map(([tool, item]) => {
      let cumulative = 0; const p95 = item.histogram.find(bucket => (cumulative += bucket.count) >= Math.ceil(item.count * 0.95));
      return [tool, { ...item, mean_ms: item.sum_ms / item.count, peak_ms: item.max_ms, p95_upper_bound_ms: p95.upper_ms, p95_method: 'bounded histogram upper bound; null means over 40000 ms' }];
    }));
  }
}

// An exact full read is a new observation. Failure to retain it never erases the known disposition.
export async function readJobDisposition(call, observed) {
  ensure(typeof observed?.operation_id === 'string' && (TERMINAL.has(observed.state) || observed.state === 'paused'), 'A known job disposition is required.');
  try {
    const full = await call('get_operation', { operation_id: observed.operation_id, view: 'full' });
    ensure(full.operation_id === observed.operation_id && full.state === observed.state && full.request_id === observed.request_id, 'Full job disposition identity/state differs from the observed receipt.');
    for (const key of ['job_id', 'requested', 'placed']) if (observed.result?.[key] !== undefined)
      ensure(full.result?.[key] === observed.result[key], 'Full job disposition count/identity differs from the observed receipt.');
    return { observed, full, details_available: true };
  } catch (error) {
    return { observed, details_available: false, details_error: { code: error.code || 'JOB_DETAILS_UNAVAILABLE', message: String(error.message).slice(0, 512) } };
  }
}

export async function runSoak(options) {
  const evidence = await DurableEvidence.create(options.evidenceDir);
  const start = performance.now(); let workloadStart = null, deadline = Infinity, timingGuard = null;
  const report = { schema_version: 1, started_at: new Date().toISOString(), status: 'running', host_node: process.versions.node,
    duration_hours_requested: options.durationHours, placements_requested: options.placements, simulation_only: true,
    mock_execution: false, execution_route: 'official-mcp-sdk-stdio-to-bundled-server-to-native-OpenPnP', hardware_qualified: false };
  const metrics = new SampleSummary(), nodeMetrics = new SampleSummary(), rssMetrics = new SampleSummary(), latencies = new Latencies();
  let client, session, capabilities, initial, part, initialInventory, latestInventory, completed, currentOperation, activeJob, mcpSnapshot;
  const mcpDirectory = path.join(options.evidenceDir, 'mcp-runtime');
  const progressReads = {};
  const dispositionReads = new Map();
  let lastJobDisposition;
  let lastRenewal = start, lastSample = -Infinity, lastEvent = start, eventCursor = 0, resyncCount = 0, ambientCycles = 0, lastFeedCount = 0;
  let stopReason = null, failure = null, closing = false;
  let nativeProcessIdentity = null, pairedBaseline = null;
  let perFeederCounts = new Map();
  const signal = name => { stopReason ??= name; };
  const onInt = () => signal('SIGINT'), onTerm = () => signal('SIGTERM');
  process.on('SIGINT', onInt); process.on('SIGTERM', onTerm);
  const checkStop = () => { if (stopReason || performance.now() >= deadline) fail('SOAK_INCOMPLETE', stopReason || 'Requested duration ended before the current operation completed.'); };
  const call = async (name, args = {}, raw = false) => {
    const before = performance.now(); let failed = true;
    try {
      let response;
      try { response = await client.callTool({ name: `openpnp_${name}`, arguments: args }, undefined, { timeout: 25000 }); }
      catch { fail('MCP_TRANSPORT_ERROR', `MCP transport did not return a response to ${name}.`); }
      if (response.isError) throw Object.assign(new Error(response.structuredContent?.error?.message || `MCP ${name} failed.`), { code: response.structuredContent?.error?.code || 'MCP_TOOL_ERROR' });
      if (!record(response.structuredContent)) fail('MCP_TRANSPORT_ERROR', `MCP ${name} returned no structured receipt.`);
      const complete = await readCompleteResponse(response.structuredContent, pageArgs => call('read_response_page', pageArgs));
      if (args.view === 'progress') {
        ensure(complete.response_view?.name === 'progress' && complete.response_view.retained_snapshot === false && complete.truncated !== true, 'Requested progress view was not returned intact.');
        progressReads[name] = (progressReads[name] ?? 0) + 1;
      }
      failed = false; return raw ? { ...response, structuredContent: complete } : complete;
    } finally { latencies.add(name, performance.now() - before, failed); }
  };
  const mutation = async (name, args = {}) => {
    if (!closing) checkStop();
    const requestId = randomUUID();
    const params = { request_id: requestId, ...(session ? { session_id: session } : {}), ...args };
    // Persist admission intent before the first and only effect call; never persist the bearer lease.
    await evidence.append('mutation_intent', { tool: `openpnp_${name}`, request_id: requestId, ...(args.operation_id ? { operation_id: args.operation_id } : {}) });
    const handle = await dispatchMutationOnce(call, name, params, unknown => evidence.append('outcome_unknown', unknown));
    await evidence.append('mutation_receipt', { tool: `openpnp_${name}`, request_id: requestId, operation_id: handle.operation_id, state: handle.state });
    return handle;
  };
  const inspectStatus = status => {
    ensure(status.bridge_instance_id === initial.bridge_instance_id && status.machine_id === initial.machine_id, 'Machine or bridge identity changed during the run.');
    ensure(status.journal_fault === false && status.configuration_fault === false, 'Native journal/configuration fault was raised.');
    checkMetrics(status.metrics, capabilities.bridge.limits, { requireQualificationBounds: !options.smoke });
    if (status.response_view?.name === 'progress') ensure(status.machine?.feeders_complete === true, 'Progress feeder inventory is incomplete; this workload requires the complete selected supply.');
    const inventory = inventoryFor(status.machine, part.id);
    ensure(JSON.stringify(inventory.feeders.map(({ id, capacity }) => ({ id, capacity }))) === JSON.stringify(initialInventory.feeders.map(({ id, capacity }) => ({ id, capacity }))), 'Selected native supply identity/capacity changed.');
    ensure(inventory.feed_count >= lastFeedCount && inventory.feed_count <= options.placements, 'Native feed count reset or exceeded the finite workload.');
    for (const feeder of inventory.feeders) {
      ensure(feeder.feed_count >= (perFeederCounts.get(feeder.id) ?? 0), 'An individual native feeder count decreased.');
      perFeederCounts.set(feeder.id, feeder.feed_count);
    }
    lastFeedCount = inventory.feed_count; latestInventory = inventory;
    return status;
  };
  const sample = async (force = false) => {
    if (!force && performance.now() - lastSample < 10000) return;
    const nativeReadStartedElapsedMs = performance.now() - start;
    const status = inspectStatus(await call('get_status', { view: 'progress' }));
    const nativeReadCompletedElapsedMs = performance.now() - start;
    const memory = process.memoryUsage(); metrics.add(status.metrics); nodeMetrics.add(memory);
    let nativeProcess = null;
    if (options.nativePid) {
      const { stdout } = await execFileAsync('ps', ['-p', String(options.nativePid), '-o', 'rss=,lstart=,comm='], { timeout: 5000, maxBuffer: 16384, env: { ...process.env, LC_ALL: 'C' } });
      nativeProcess = parseNativeProcess(stdout);
      const identity = `${nativeProcess.started_at_ps}|${nativeProcess.executable}`;
      ensure(nativeProcessIdentity === null || nativeProcessIdentity === identity, 'The supplied JVM PID was reused or its process identity changed.');
      nativeProcessIdentity = identity; rssMetrics.add({ rss_bytes: nativeProcess.rss_bytes });
    }
    const snapshotTime = Date.parse(status.machine.snapshot_at);
    const sampleEvidence = { observed_at: new Date().toISOString(), elapsed_ms: performance.now() - start,
      native_read_started_elapsed_ms: nativeReadStartedElapsedMs, native_read_completed_elapsed_ms: nativeReadCompletedElapsedMs, bridge_instance_id: status.bridge_instance_id,
      config_revision: status.config_revision, through_sequence: status.through_sequence, job_state: status.job_state,
      active_operation_id: status.active_operation_id, machine_snapshot_at: status.machine.snapshot_at,
      native_busy: status.native_busy, job_progress: status.job_progress,
      machine_snapshot_age_ms: Number.isFinite(snapshotTime) ? Math.max(0, Date.now() - snapshotTime) : null,
      native_metrics: status.metrics, harness_memory: memory, native_process: nativeProcess ? { ...nativeProcess, pid: options.nativePid } : null, inventory: latestInventory };
    await evidence.append('sample', sampleEvidence);
    timingGuard?.add(sampleEvidence);
    lastSample = performance.now();
  };
  const slowEvents = async (force = false) => {
    if (!eventPollDue(performance.now(), lastEvent, options.eventPollSeconds ?? 60, force)) return;
    const cursorBefore = eventCursor;
    const page = await call('get_events', { after_sequence: eventCursor, limit: 1 });
    ensure(Array.isArray(page.events) && typeof page.resync_required === 'boolean', 'Invalid native event page.');
    if (page.resync_required) {
      const status = inspectStatus(await call('get_status', { view: 'progress' })); const configuration = await call('get_configuration');
      ensure(Number.isSafeInteger(status.through_sequence), 'Missing authoritative snapshot sequence during event resync.');
      ensure(configuration.config_revision === status.config_revision, 'Configuration changed while event resync snapshots were read.');
      eventCursor = status.through_sequence; resyncCount++;
      await evidence.save(`resync-${resyncCount}.json`, { status, configuration, event_gap: { after_sequence: cursorBefore, oldest_sequence: page.oldest_sequence, latest_sequence: page.latest_sequence } });
    } else {
      ensure(Number.isSafeInteger(page.through_sequence) && page.through_sequence >= eventCursor, 'Native event cursor regressed.');
      eventCursor = page.through_sequence;
    }
    await evidence.append('slow_consumer', { after_sequence: cursorBefore, next_sequence: eventCursor, latest_sequence: page.latest_sequence,
      resync_required: page.resync_required, delivered_count: page.events.length, resync_count: resyncCount });
    lastEvent = performance.now();
  };
  const supervise = async () => {
    checkStop();
    if (session && performance.now() - lastRenewal >= 60000) {
      await call('renew_control_session', { session_id: session, ttl_seconds: 600 }); lastRenewal = performance.now();
      await evidence.append('lease_renewed', { elapsed_ms: lastRenewal - start });
    }
    await sample(); await slowEvents();
  };
  const retainJobDisposition = async observed => {
    const key = `${observed.operation_id}:${observed.state}`;
    if (!dispositionReads.has(key)) {
      // Save the bounded known result before the separate full read can fail.
      await evidence.append('job_disposition_observed', { operation: observed });
      const retained = await readJobDisposition(call, observed); dispositionReads.set(key, retained); lastJobDisposition = retained;
      await evidence.save(`job-disposition-${dispositionReads.size}.json`, retained);
    }
    return dispositionReads.get(key);
  };
  const observe = async (handle, { timeoutMs = 120000, job = false } = {}) => {
    const began = performance.now(); currentOperation = handle.operation_id;
    ensure(typeof currentOperation === 'string', 'Native operation ID is absent.');
    let operation = handle;
    while (!TERMINAL.has(operation.state)) {
      if (job && operation.state === 'paused') await retainJobDisposition(operation);
      ensure(['accepted', 'running'].includes(operation.state), `Unexpected native operation state: ${operation.state}`);
      await supervise();
      if (performance.now() - began > timeoutMs) fail('OPERATION_TIMEOUT', `Operation ${currentOperation} exceeded its observation deadline; no retry was made.`);
      await delay(job ? 1000 : 100); operation = await call('get_operation', { operation_id: currentOperation, ...(job ? { view: 'progress' } : {}) });
      ensure(operation.operation_id === currentOperation, 'Native operation identity changed.');
    }
    await evidence.append('operation_terminal', { operation_id: operation.operation_id, state: operation.state, elapsed_ms: performance.now() - began,
      result: job ? { job_id: operation.result?.job_id, requested: operation.result?.requested, placed: operation.result?.placed } : operation.result });
    const retained = job ? await retainJobDisposition(operation) : null;
    if (operation.state !== 'succeeded') fail(operation.state === 'outcome_unknown' || operation.state === 'unknown' ? 'OUTCOME_UNKNOWN' : 'NATIVE_OPERATION_FAILED', `Native operation ${operation.operation_id} ended ${operation.state}.`);
    await waitForNativeIdle(async () => inspectStatus(await call('get_status', { view: 'progress' })), { required: !options.smoke, supervise });
    if (job) {
      if (!retained.details_available) fail(retained.details_error.code, `Known succeeded job ${operation.operation_id}; full details unavailable: ${retained.details_error.message}`);
      operation = retained.full;
    }
    currentOperation = null; return operation;
  };
  const operation = async (name, args = {}, settings = {}) => observe(await mutation(name, args), settings);
  try {
    await evidence.append('run_started', { ...report, smoke: options.smoke }); await evidence.save('report.json', report);
    mcpSnapshot = await snapshotMcpDirectory(path.join(ROOT, 'plugins/openpnp/mcp'), mcpDirectory);
    const serverPath = path.join(mcpDirectory, 'server.mjs');
    report.mcp_runtime_snapshot = { ...mcpSnapshot, relative_directory: 'mcp-runtime', execution_path: '<evidence-dir>/mcp-runtime/server.mjs' };
    await evidence.save('mcp-runtime-manifest.json', report.mcp_runtime_snapshot);
    await evidence.append('mcp_runtime_snapshotted', { tree_sha256: mcpSnapshot.tree_sha256, server_sha256: mcpSnapshot.server_sha256, file_count: mcpSnapshot.files.length });
    report.harness_sha256 = hash(await readFile(fileURLToPath(import.meta.url)));
    report.timing_guard_sha256 = hash(await readFile(path.join(ROOT, 'scripts/openpnp-soak-timing.mjs')));
    report.direct_http_client_sha256 = hash(await readFile(path.join(ROOT, 'src/openpnp/node/client.mjs')));
    report.bundled_mcp_sha256 = mcpSnapshot.server_sha256;
    report.packaged_bridge_manifest = JSON.parse(await readFile(path.join(ROOT, 'plugins/openpnp/bridge/build-manifest.json'), 'utf8'));
    ensure(hash(await readFile(path.join(ROOT, 'plugins/openpnp/bridge/openpnp-codex-bridge.jar'))) === report.packaged_bridge_manifest.bridge_sha256,
      'The local packaged bridge JAR does not match its manifest.');
    report.packaged_bridge_manifest_provenance = 'Local package metadata; not cryptographic attestation of the serving process.';
    if (options.nativeBuildManifest) {
      ensure((await stat(options.nativeBuildManifest)).size <= 4 * 1024 * 1024, 'Supplied native build manifest is too large.');
      const bytes = await readFile(options.nativeBuildManifest), manifest = JSON.parse(bytes.toString('utf8'));
      report.supplied_native_build_manifest = { sha256: hash(bytes), upstream_commit: manifest.upstream_commit, bridge_version: manifest.bridge_version,
        bridge_sha256: manifest.bridge_sha256, provenance: 'Caller-supplied local manifest; endpoint binding is not independently attested.' };
      await evidence.save('supplied-native-build-manifest.json', manifest);
    }
    const state = path.join(options.evidenceDir, 'mcp-state'); await mkdir(state, { mode: 0o700 });
    await verifyMcpSnapshot(mcpDirectory, mcpSnapshot);
    const transport = new StdioClientTransport({ command: process.execPath, args: [serverPath, '--stdio'], cwd: state,
      env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: options.connectionFile }, stderr: 'pipe' });
    // Drain stderr without retaining arbitrary process output or allowing an unread pipe to stall stdio.
    let stderrBytes = 0; transport.stderr?.on('data', bytes => { stderrBytes += bytes.length; });
    client = new Client({ name: 'openpnp-native-soak', version: '1.0.0' }); await client.connect(transport);
    capabilities = await call('get_capabilities'); initial = await call('get_status');
    report.native_job_order_check = { required: !options.smoke, required_value: !options.smoke ? 'Unsorted' : null,
      observed: capabilities.bridge.native_job_order ?? null,
      attested: capabilities.bridge.simulator_attestation.native_job_order ?? null,
      rationale: capabilities.bridge.simulator_attestation.job_order_rationale ?? 'Not advertised by this native build.',
      interpretation: options.smoke ? 'Explicit smoke only; absent or older recipe values do not establish the current full-soak admission requirement.'
        : 'Actual native Unsorted job order is required for the already ordered sustained grid; default-profile ordering and physical throughput are separate.' };
    ({ part, inventory: initialInventory } = validatePrerequisites(capabilities, initial, options.placements, { requireQualificationBounds: !options.smoke })); latestInventory = initialInventory;
    report.native_resource_bound_checks = {
      artifact_storage_bytes: Number.isSafeInteger(capabilities.bridge.limits.artifact_storage_bytes)
        ? { checked: true, cap: capabilities.bridge.limits.artifact_storage_bytes }
        : { checked: false, reason: 'Older explicit smoke build does not advertise aggregate artifact storage bound.' },
      heap_max_at_most_2gib: { required: !options.smoke, observed: initial.metrics.heap_max_bytes, satisfied: initial.metrics.heap_max_bytes <= 2 * 1024 ** 3 },
      native_idle_observation: { required: !options.smoke, available: typeof initial.native_busy === 'boolean' },
    };
    report.loaded_bridge_binding = verifyNativeBuildBinding(capabilities.bridge, report.packaged_bridge_manifest, { required: !options.smoke });
    workloadStart = performance.now(); deadline = workloadStart + options.durationHours * 3600000;
    timingGuard = new SoakTimingGuard({ workloadStartElapsedMs: workloadStart - start });
    report.timing_window = { observation_schema_version: 1, workload_started_elapsed_ms: workloadStart - start, policy: TIMING_POLICY };
    eventCursor = initial.through_sequence;
    report.bridge = capabilities.bridge; report.initial_inventory = initialInventory;
    report.supplied_native_build_manifest ??= { provenance: 'Not supplied; no serving binary hash claimed.' };
    await evidence.save('initial-state.json', { capabilities, status: initial, configuration: await call('get_configuration') });
    // A bounded, paired read-only baseline includes transport/auth parsing in both channels.
    // These are wall-clock samples on this host, not an independently controlled placement-speed experiment.
    const directClient = new BridgeClient({ connectionFile: options.connectionFile });
    const directTimes = [], mcpTimes = [];
    for (let index = 0; index < 20; index++) {
      checkStop();
      const directStart = performance.now(); const directStatus = await directClient.call('openpnp_get_status', {});
      directTimes.push(performance.now() - directStart); inspectStatus(directStatus);
      const mcpStart = performance.now(); inspectStatus(await call('get_status')); mcpTimes.push(performance.now() - mcpStart);
    }
    pairedBaseline = { direct_authenticated_http: latencyStats(directTimes), official_mcp_stdio: latencyStats(mcpTimes),
      per_pair_overhead_ms: latencyStats(mcpTimes.map((value, index) => Math.max(0, value - directTimes[index]))),
      signed_mean_overhead_ms: mcpTimes.reduce((sum, value, index) => sum + value - directTimes[index], 0) / mcpTimes.length,
      sampling: '20 sequential paired get_status reads before the job; direct path includes connection/token file reads. Nonnegative overhead distribution clips negative scheduling noise; signed mean is preserved.' };
    await evidence.save('latency-baseline.json', pairedBaseline); await sample(true);
    const grant = await mutation('request_control_session', { ttl_seconds: 600 }); session = grant.session_id;
    ensure(typeof session === 'string' && session, 'Native lease grant is missing.'); lastRenewal = performance.now();
    await operation('set_machine_enabled', { enabled: true }); await operation('home_machine');
    const csv = makeWorkloadCsv(part, options.placements); await evidence.save('workload.csv', csv);
    report.csv_sha256 = hash(csv); report.workload = { board_width_mm: 150, board_height_mm: 150, pitch_mm: 1.5, columns: 100, part_id: part.id, package_id: part.package_id, height_mm: part.height_mm };
    const imported = await call('import_job', { format: 'reference-csv', content: csv, units: 'mm', widthMm: 150, heightMm: 150, boardId: 'sustained-board', jobId: 'sustained-workload' });
    report.job_artifact_id = imported.artifact_id;
    const validatedImport = await call('validate_imported_job', { artifact_id: imported.artifact_id, mode: 'offline' });
    await evidence.save('import-validation.json', { imported, validatedImport });
    ensure(imported.validation?.valid === true && validatedImport.validation?.valid === true, 'Canonical offline validation failed.');
    const artifact = await call('get_artifact', { artifact_id: imported.artifact_id }); await evidence.save('canonical-job.json', artifact);
    const prepared = await operation('prepare_job', { artifact_id: imported.artifact_id });
    ensure(prepared.result?.requested === options.placements, 'Native prepared job count differs from requested workload.');
    const validated = await operation('validate_job'); ensure(validated.result?.valid === true, 'Native model validation failed.');
    // Native processor preflight still executes when start_job runs. This model check does not replace it.
    const handle = await mutation('start_job', { job_id: prepared.result.job_id }); activeJob = handle.operation_id;
    const jobStarted = performance.now(); completed = await observe(handle, { timeoutMs: options.durationHours * 3600000, job: true });
    report.native_job_elapsed_ms = performance.now() - jobStarted;
    await evidence.save('completed-job.json', completed);
    ensure(completed.result?.requested === options.placements && completed.result?.placed === options.placements && completed.result?.independently_inspected === 0,
      'Native job did not report the exact workload with inspection limits.');
    activeJob = null; await sample(true); ensure(latestInventory.feed_count === options.placements, 'Native feed count does not match completed placements.');
    await slowEvents(true);
    let nextAmbient = performance.now();
    while (!stopReason && performance.now() < deadline) {
      await supervise();
      if (performance.now() >= nextAmbient && deadline - performance.now() >= 30000) {
        const plan = await call('plan_motion', { session_id: session, x: 10 + ambientCycles % 2, y: 10, z: 0, rotation: 0, units: 'mm', speed: 0.1 });
        await operation('execute_motion', { plan_id: plan.plan_id });
        const camera = await operation('capture_camera', { mode: ambientCycles % 2 ? 'raw' : 'settled' });
        const image = await call('get_native_artifact', { artifact_id: camera.result.artifact_id }, true);
        const frame = image.content.find(item => item.type === 'image' && item.mimeType === 'image/png');
        ensure(frame && hash(Buffer.from(frame.data, 'base64')) === camera.result.sha256 && image.structuredContent.sha256 === camera.result.sha256, 'Native camera artifact bytes do not match their capture digest.');
        ambientCycles++; await evidence.append('ambient_cycle', { cycle: ambientCycles, artifact_id: camera.result.artifact_id, sha256: camera.result.sha256, size: camera.result.size });
        nextAmbient = performance.now() + 30000;
      }
      await delay(Math.min(1000, Math.max(1, deadline - performance.now())));
    }
    if (stopReason) fail('SOAK_INCOMPLETE', `Stopped by ${stopReason}.`);
    report.mcp_stderr_bytes = stderrBytes;
  } catch (error) {
    // A timer crossing between the idle-loop condition and supervision is normal completion.
    if (!(error.code === 'SOAK_INCOMPLETE' && !stopReason && completed && !currentOperation && performance.now() >= deadline)) {
      failure = { code: error.code || 'SOAK_FAILED', message: error.message };
      await evidence.append('run_error', failure);
    }
  } finally {
    closing = true;
    const cleanupEvidence = async (type, value) => {
      try { await evidence.append(type, value); }
      catch { failure ??= { code: 'EVIDENCE_WRITE_FAILED', message: 'Evidence writes failed during cleanup; inspect retained progress.' }; }
    };
    // A stopped client is not a stopped machine. Observe the known job, request a cooperative pause once,
    // and release this lease. Never abort, home, clear an unknown, or repeat an unresolved mutation.
    if (client && session && activeJob) {
      try {
        let observed = await call('get_operation', { operation_id: activeJob, view: 'progress' });
        if (['accepted', 'running'].includes(observed.state)) {
          await mutation('pause_job', { operation_id: activeJob }); const stopDeadline = performance.now() + 30000;
          do { observed = await call('get_operation', { operation_id: activeJob, view: 'progress' }); if (observed.state === 'paused' || TERMINAL.has(observed.state)) break; await delay(100); } while (performance.now() < stopDeadline);
        }
        if (observed.state === 'paused' || TERMINAL.has(observed.state)) await retainJobDisposition(observed);
        await cleanupEvidence('cleanup_job_observation', { operation_id: activeJob, state: observed.state, cooperative_pause_confirmed: observed.state === 'paused' });
      } catch (error) { await cleanupEvidence('cleanup_unconfirmed', { action: 'observe/pause known job', code: error.code || 'UNKNOWN' }); }
    }
    if (client && session) {
      try { await call('release_control_session', { session_id: session }); await cleanupEvidence('lease_released', {}); }
      catch (error) { failure ??= { code: 'CLEANUP_UNCONFIRMED', message: 'The ownership lease release could not be confirmed.' }; await cleanupEvidence('cleanup_unconfirmed', { action: 'release lease', code: error.code || 'UNKNOWN' }); }
    }
    if (client && initial && part) {
      try { await sample(true); } catch (error) { failure ??= { code: error.code || 'FINAL_SNAPSHOT_FAILED', message: error.message }; }
    }
    if (client) try { await client.close(); } catch { /* Durable report still records any earlier uncertainty. */ }
    if (mcpSnapshot) {
      try { await verifyMcpSnapshot(mcpDirectory, mcpSnapshot); report.mcp_snapshot_unchanged_after_run = true; }
      catch (error) {
        report.mcp_snapshot_unchanged_after_run = false;
        failure ??= { code: 'MCP_SNAPSHOT_CHANGED', message: error.message };
        await cleanupEvidence('mcp_snapshot_integrity_failed', { code: 'MCP_SNAPSHOT_CHANGED' });
      }
    }
    const elapsedMs = performance.now() - start;
    const interrupted = Boolean(stopReason) || failure?.code === 'SOAK_INCOMPLETE';
    const workloadElapsedMs = workloadStart === null ? 0 : performance.now() - workloadStart;
    const qualification = qualifyResult({ smoke: options.smoke, elapsedMs: workloadElapsedMs, placements: options.placements, completed, inventory: latestInventory, resyncCount, ambientCycles, failure, interrupted,
      rssMeasured: rssMetrics.count > 0, baselineMeasured: pairedBaseline !== null });
    Object.assign(report, { completed_at: new Date().toISOString(), elapsed_ms: elapsedMs,
      status: failure ? interrupted ? 'incomplete' : 'failed' : 'completed', failure, stop_reason: stopReason, ...qualification,
      final_inventory: latestInventory, native_job: completed ? { operation_id: completed.operation_id, state: completed.state, requested: completed.result?.requested, placed: completed.result?.placed,
        independently_inspected: completed.result?.independently_inspected, placements_per_second: completed.result?.placed / (report.native_job_elapsed_ms / 1000) } : null,
      workload_elapsed_ms: workloadElapsedMs, native_metrics: metrics.snapshot(), harness_memory: nodeMetrics.snapshot(), mcp_latency: latencies.snapshot(),
      paired_read_latency_baseline: pairedBaseline, native_process_rss: { supplied_pid: options.nativePid ?? null, identity: nativeProcessIdentity,
        provenance: options.nativePid ? 'Read-only ps of caller-supplied local JVM PID; stable executable/start time checked. PID-to-endpoint binding is caller-provided.' : 'No JVM PID supplied.', ...rssMetrics.snapshot() },
      polling: { event_poll_seconds: options.eventPollSeconds ?? 60, status_sample_interval_seconds: 10, lease_renewal_interval_seconds: 60, progress_reads: progressReads, full_terminal_job_receipt: Boolean(completed), disposition_full_read_attempts: dispositionReads.size, last_known_job_disposition: lastJobDisposition ? { operation_id: lastJobDisposition.observed.operation_id, state: lastJobDisposition.observed.state, details_available: lastJobDisposition.details_available, details_error: lastJobDisposition.details_error } : null, supply_view: 'complete bounded feeder inventory required', full_status_baseline: 'initialization and 20 paired reads before the job', interpretation: 'Progress reads archive no full responses; occasional exact final/event/configuration evidence remains retained.' },
      event_resync_count: resyncCount, ambient_cycles: ambientCycles, machine_state_after_cleanup: 'Observe the final sample; lease release requests a cooperative pause and is not an emergency stop.' });
    report.independent_timing_gate = (timingGuard ?? new SoakTimingGuard()).finish({
      closed: report.status !== 'running', reportStatus: report.status, rawSimulatorSoakComplete: report.simulator_soak_complete, workloadEndElapsedMs: elapsedMs });
    report.continuous_simulator_soak_qualified = report.independent_timing_gate.continuous_simulator_soak_qualified;
    await evidence.append('run_finished', { status: report.status, simulator_soak_complete: report.simulator_soak_complete, elapsed_ms: elapsedMs, hardware_qualified: false });
    await evidence.save('report.json', report); await evidence.close();
    process.removeListener('SIGINT', onInt); process.removeListener('SIGTERM', onTerm);
  }
  return report;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const options = parseOptions(process.argv.slice(2));
    if (options.help) process.stdout.write('Usage: node scripts/openpnp-soak.mjs --connection-file /absolute/connection.json --evidence-dir /absolute/new-run [--duration-hours 8] [--placements 10000] [--native-build-manifest /absolute/manifest.json] [--native-pid JVM_PID] [--event-poll-seconds 60..600]\nShort or reduced-count runs are explicitly unqualified smoke runs. Only a fresh sustained-workload native simulator is accepted.\n');
    else {
      const result = await runSoak(options);
      process.stdout.write(JSON.stringify({ status: result.status, simulator_soak_complete: result.simulator_soak_complete, continuous_simulator_soak_qualified: result.continuous_simulator_soak_qualified, hardware_qualified: false, report: path.join(options.evidenceDir, 'report.json') }) + '\n');
      process.exitCode = result.status === 'completed' && (options.smoke || result.continuous_simulator_soak_qualified) ? 0 : result.status === 'incomplete' ? 2 : 1;
    }
  } catch (error) { process.stderr.write(`OpenPnP soak: ${error.code || 'ERROR'}: ${error.message}\n`); process.exitCode = 1; }
}
