// Harness policy/format tests only. Synthetic observations and callbacks below never qualify OpenPnP or hardware.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, mkdir, writeFile, chmod, symlink } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import os from 'node:os';
import path from 'node:path';
import { parseOptions, validatePrerequisites, inventoryFor, makeWorkloadCsv, checkMetrics,
  dispatchMutationOnce, SampleSummary, parseNativeProcess, latencyStats, qualifyResult, runSoak, verifyNativeBuildBinding, waitForNativeIdle, snapshotMcpDirectory, verifyMcpSnapshot, PINNED_UPSTREAM } from '../../scripts/openpnp-soak.mjs';
import { TOOL_DEFINITIONS } from '../../src/openpnp/node/contracts.mjs';
import { prepareJob, flattenJob, validateJob } from '../../plugins/openpnp/mcp/domain/index.mjs';

const argv = ['--connection-file', '/tmp/soak-connection.json', '--evidence-dir', '/tmp/soak-evidence'];
const part = { id: 'R0603-1K', package_id: 'R0603', height_mm: 0.75 };

test('isolated MCP copy executes its hashed nested modules after the source changes and rejects snapshot tampering', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-snapshot-fixture-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = path.join(root, 'source'), copied = path.join(root, 'copied');
  await mkdir(path.join(source, 'domain'), { recursive: true });
  await writeFile(path.join(source, 'server.mjs'), 'import { value } from "./domain/value.mjs"; process.stdout.write(value);\n');
  await writeFile(path.join(source, 'domain/value.mjs'), 'export const value = "original-copy";\n');
  await writeFile(path.join(source, 'tool-inputs.json'), '{"fixture":true}\n');
  const manifest = await snapshotMcpDirectory(source, copied);
  assert.deepEqual(manifest.files.map(file => file.path), ['domain/value.mjs', 'server.mjs', 'tool-inputs.json']);
  assert.match(manifest.tree_sha256, /^[a-f0-9]{64}$/);
  await writeFile(path.join(source, 'domain/value.mjs'), 'export const value = "later-source";\n');
  await writeFile(path.join(source, 'server.mjs'), 'throw new Error("workspace changed");\n');
  await verifyMcpSnapshot(copied, manifest);
  const { stdout } = await promisify(execFile)(process.execPath, [path.join(copied, 'server.mjs')]);
  assert.equal(stdout, 'original-copy', 'This tiny module fixture tests copied code execution, not OpenPnP qualification.');
  await assert.rejects(snapshotMcpDirectory(source, copied), { code: 'EEXIST' });
  await chmod(path.join(copied, 'domain/value.mjs'), 0o600);
  await writeFile(path.join(copied, 'domain/value.mjs'), 'export const value = "tampered-copy";\n');
  await assert.rejects(verifyMcpSnapshot(copied, manifest), /snapshot changed/);
  await symlink(path.join(source, 'server.mjs'), path.join(source, 'linked.mjs'));
  await assert.rejects(snapshotMcpDirectory(source, path.join(root, 'linked-copy')), /rejects links/);
});
function fixture() {
  const limits = { canonical_placements: 10000, plans: 128, artifact_metadata_cache: 128, retained_requests: 20000, journal_bytes: 512 * 1024 * 1024, artifact_storage_bytes: 1024 ** 3 };
  const metrics = { uptime_ms: 1, heap_used_bytes: 100, heap_committed_bytes: 200, heap_max_bytes: 300, event_buffer_count: 0,
    operation_count: 0, request_count: 0, plan_count: 0, artifact_count: 0, artifact_metadata_cache_count: 0,
    artifact_bytes: 0, artifact_memory_content_bytes: 0, journal_bytes: 1 };
  const capabilities = { connected: true, tools: TOOL_DEFINITIONS.map(tool => tool.name), bridge: { upstream_commit: PINNED_UPSTREAM,
    bridge_version: '0.1.0', protocol_version: '1.0', schema_version: 1, machine_id: 'machine', bridge_instance_id: 'instance', simulation: true,
    hardware_qualified: false, simulator_profile: 'sustained-workload', native_job_order: 'Unsorted', limits,
    simulator_attestation: { profile: 'sustained-workload', fixture_provenance: 'native-finite-tray-supply-zero-pitch',
      driver_class: 'org.openpnp.machine.reference.driver.NullDriver', checked_at_dispatch: true, native_job_order: 'Unsorted' } } };
  const status = { machine_id: 'machine', bridge_instance_id: 'instance', job_state: 'absent', job_id: null, active_operation_id: null, native_busy: false,
    configuration_fault: false, journal_fault: false, metrics,
    machine: { parts: [{ ...part }], drivers: [{ class: 'org.openpnp.machine.reference.driver.NullDriver' }], feeders: [
      { id: 'finite-tray', part_id: part.id, class: 'org.openpnp.machine.reference.feeder.ReferenceTrayFeeder', enabled: true, virtual_supply: true, capacity: 10000, feed_count: 0 },
    ] } };
  return { capabilities, status, limits, metrics };
}

test('soak CLI defaults to eight hours and 10000 placements, explicitly classifies reduced runs', () => {
  assert.deepEqual(parseOptions(argv), { connectionFile: argv[1], evidenceDir: argv[3], durationHours: 8, placements: 10000, smoke: false });
  assert.equal(parseOptions([...argv, '--duration-hours', '0.01']).smoke, true);
  assert.equal(parseOptions([...argv, '--placements', '100']).smoke, true);
  assert.equal(parseOptions([...argv, '--native-pid', '123']).nativePid, 123);
  for (const extra of [['--duration-hours', 'NaN'], ['--duration-hours', '0'], ['--duration-hours', '25'], ['--placements', '10001'],
    ['--placements', '2.5'], ['--placements'], ['--native-pid', '-1'], ['--native-pid', '1;kill'], ['--mystery', 'x'], ['--evidence-dir', '/tmp/duplicate']]) {
    assert.throws(() => parseOptions([...argv, ...extra]));
  }
  assert.throws(() => parseOptions(['--connection-file', 'relative.json', '--evidence-dir', '/tmp/evidence']));
});

test('fresh native fixture admission rejects wrong identity, physical class, reused stock, faults and missing capabilities', async t => {
  const valid = fixture(); assert.equal(validatePrerequisites(valid.capabilities, valid.status, 10000).inventory.remaining, 10000);
  const changes = {
    disconnected: f => { f.capabilities.connected = false; },
    unpinned: f => { f.capabilities.bridge.upstream_commit = 'other'; },
    ordinary_fixture: f => { f.capabilities.bridge.simulator_profile = 'default'; },
    hardware_claim: f => { f.capabilities.bridge.hardware_qualified = true; },
    physical_driver: f => { f.status.machine.drivers[0].class = 'org.openpnp.machine.reference.driver.GcodeDriver'; },
    different_instance: f => { f.status.bridge_instance_id = 'restarted'; },
    stale_job: f => { f.status.job_state = 'completed'; },
    active_operation: f => { f.status.active_operation_id = 'unknown-old-operation'; },
    fault: f => { f.status.configuration_fault = true; },
    reused_supply: f => { f.status.machine.feeders[0].feed_count = 1; },
    infinite_supply: f => { f.status.machine.feeders[0].capacity = Infinity; },
    missing_camera_tool: f => { f.capabilities.tools = f.capabilities.tools.filter(name => name !== 'openpnp_capture_camera'); },
    height_unknown: f => { delete f.status.machine.parts[0].height_mm; },
  };
  for (const [name, change] of Object.entries(changes)) await t.test(name, () => { const f = fixture(); change(f); assert.throws(() => validatePrerequisites(f.capabilities, f.status, 10000)); });
});

test('finite inventory aggregates native fallback feeders and rejects over-consumption', () => {
  const { status } = fixture(); const first = status.machine.feeders[0]; first.feed_count = 10000;
  status.machine.feeders.push({ ...first, id: 'fallback', capacity: 100, feed_count: 7 });
  assert.deepEqual(inventoryFor(status.machine, part.id), { capacity: 10100, feed_count: 10007, remaining: 93,
    feeders: [{ id: 'finite-tray', capacity: 10000, feed_count: 10000 }, { id: 'fallback', capacity: 100, feed_count: 7 }] });
  status.machine.feeders[1].feed_count = 101; assert.throws(() => inventoryFor(status.machine, part.id));
});

test('actual canonical importer accepts all 10000 generated centers with exact native part metadata and finite bounds', () => {
  const csv = makeWorkloadCsv(part, 10000);
  const job = prepareJob({ format: 'reference-csv', content: csv, units: 'mm', widthMm: 150, heightMm: 150, boardId: 'sustained-board', jobId: 'sustained-workload' });
  assert.equal(job.boards[0].placements.length, 10000);
  assert.equal(validateJob(job).valid, true); assert.equal(flattenJob(job).length, 10000);
  assert.equal(new Set(job.boards[0].placements.map(p => p.ref)).size, 10000);
  assert.deepEqual(job.parts.map(({ id, packageId, heightMm }) => ({ id, packageId, heightMm })), [{ id: part.id, packageId: part.package_id, heightMm: part.height_mm }]);
  const positions = job.boards[0].placements;
  assert.deepEqual([positions[0].x, positions[0].y, positions.at(-1).x, positions.at(-1).y], [0.75, 0.75, 149.25, 149.25]);
  assert.ok(positions.every(p => p.x > 0 && p.x < 150 && p.y > 0 && p.y < 150 && p.enabled && p.side === 'top'));
  assert.ok(Buffer.byteLength(JSON.stringify(job)) < 8 * 1024 * 1024, 'Complete canonical job fits the native transport ceiling.');
});

test('native metric gates reject missing evidence, retention overflow and internally inconsistent heap values', () => {
  const f = fixture(); assert.equal(checkMetrics(f.metrics, f.limits), f.metrics);
  for (const [key, value] of Object.entries({ event_buffer_count: 5001, plan_count: 129, artifact_metadata_cache_count: 129,
    request_count: 20001, journal_bytes: f.limits.journal_bytes + 1, artifact_bytes: f.limits.artifact_storage_bytes + 1, artifact_memory_content_bytes: 1, heap_used_bytes: 201, heap_max_bytes: 199 })) {
    assert.throws(() => checkMetrics({ ...f.metrics, [key]: value }, f.limits));
  }
  const missing = { ...f.metrics }; delete missing.artifact_bytes; assert.throws(() => checkMetrics(missing, f.limits));
});

test('full soak fails before work without an aggregate artifact cap or a JVM heap capped at 2 GiB', () => {
  const f = fixture(), required = { requireQualificationBounds: true };
  assert.equal(checkMetrics({ ...f.metrics, heap_max_bytes: 2 * 1024 ** 3 }, f.limits, required).heap_max_bytes, 2 * 1024 ** 3);
  assert.throws(() => checkMetrics({ ...f.metrics, heap_max_bytes: 2 * 1024 ** 3 + 1 }, f.limits, required), /2 GiB/);
  const older = { ...f.limits }; delete older.artifact_storage_bytes;
  assert.throws(() => checkMetrics(f.metrics, older, required), /aggregate storage/);
  assert.equal(checkMetrics(f.metrics, older), f.metrics, 'Older smoke may lack the new bound without being qualified.');
  f.capabilities.bridge.limits = older;
  assert.throws(() => validatePrerequisites(f.capabilities, f.status, 10000, required), /aggregate storage/);
});

test('full soak rejects absent, NozzleTips, or inconsistent native job order without reclassifying older smoke', () => {
  const required = { requireQualificationBounds: true };
  const valid = fixture(); assert.equal(validatePrerequisites(valid.capabilities, valid.status, 10000, required).inventory.remaining, 10000);
  for (const order of [undefined, 'NozzleTips', 'unsorted']) {
    const f = fixture(); f.capabilities.bridge.native_job_order = order;
    assert.throws(() => validatePrerequisites(f.capabilities, f.status, 10000, required), /actual native Unsorted/);
    assert.equal(validatePrerequisites(f.capabilities, f.status, 100).inventory.feed_count, 0, 'A reduced historical recipe is still an explicitly unqualified smoke.');
  }
  const mismatched = fixture(); mismatched.capabilities.bridge.simulator_attestation.native_job_order = 'NozzleTips';
  assert.throws(() => validatePrerequisites(mismatched.capabilities, mismatched.status, 10000, required), /actual native Unsorted/);
});

test('terminal operation follow-through waits on native wrapper release and active-operation clearance using reads only', async () => {
  const statuses = [{ native_busy: true }, { native_busy: false, active_operation_id: 'still-active' }, { native_busy: false }];
  let reads = 0, supervised = 0;
  const idle = await waitForNativeIdle(async () => statuses[reads++], { pollIntervalMs: 0, supervise: async () => { supervised++; } });
  assert.deepEqual(idle, { native_busy: false }); assert.equal(reads, 3); assert.equal(supervised, 2);
  await assert.rejects(waitForNativeIdle(async () => ({ native_busy: true }), { timeoutMs: 0 }), { code: 'NATIVE_IDLE_TIMEOUT' });
  await assert.rejects(waitForNativeIdle(async () => ({})), /absent or invalid/);
  await assert.rejects(waitForNativeIdle(async () => ({ native_busy: 'false' }), { required: false }), /absent or invalid/);
  assert.deepEqual(await waitForNativeIdle(async () => ({}), { required: false }), {}, 'Older smoke explicitly lacks this new observation.');
});

test('full soak requires the actually loaded bridge digest to match the verified package, while older smoke reports absence', () => {
  const digest = 'a'.repeat(64), packaged = { bridge_sha256: digest };
  assert.equal(verifyNativeBuildBinding({}, packaged).verified, false);
  assert.throws(() => verifyNativeBuildBinding({}, packaged, { required: true }));
  assert.equal(verifyNativeBuildBinding({ bridge_artifact_hash_available: true, bridge_artifact_sha256: digest }, packaged, { required: true }).verified, true);
  assert.throws(() => verifyNativeBuildBinding({ bridge_artifact_hash_available: true, bridge_artifact_sha256: 'b'.repeat(64) }, packaged));
});

test('lost admitted mutation is observed by its original request ID and never resent', async () => {
  const seen = []; const known = { operation_id: 'op', state: 'running' };
  const receipt = await dispatchMutationOnce(async (name, args) => {
    seen.push({ name, args });
    if (name === 'start_job') throw Object.assign(new Error('lost response'), { code: 'OUTCOME_UNKNOWN' });
    return { found: true, operation: known };
  }, 'start_job', { request_id: 'original', session_id: 'private-lease', job_id: 'job' });
  assert.deepEqual(receipt, known);
  assert.deepEqual(seen.map(call => call.name), ['start_job', 'get_request_status']);
  assert.deepEqual(seen[1].args, { request_id: 'original' });
});

test('missing admission, failed request lookup and unknown operation remain unresolved without retry', async t => {
  for (const outcome of ['not-found', 'lookup-lost', 'unknown-operation']) await t.test(outcome, async () => {
    const seen = [];
    const run = () => dispatchMutationOnce(async name => {
      seen.push(name); if (name === 'execute_motion' || outcome === 'lookup-lost') throw new Error('transport disconnected');
      if (outcome === 'unknown-operation') return { found: true, operation: { operation_id: 'op', state: 'outcome_unknown' } };
      return { found: false };
    }, 'execute_motion', { request_id: 'unchanged' });
    if (outcome === 'unknown-operation') assert.equal((await run()).state, 'outcome_unknown');
    else await assert.rejects(run, error => error.code === 'OUTCOME_UNKNOWN');
    assert.deepEqual(seen, ['execute_motion', 'get_request_status']);
  });
});

test('known rejection is not reinterpreted as admission and lost session grants require durable success', async () => {
  const calls = [];
  await assert.rejects(() => dispatchMutationOnce(async name => { calls.push(name); throw Object.assign(new Error('blocked'), { code: 'CONFIGURATION_FAULT' }); }, 'home_machine', { request_id: 'one' }), { code: 'CONFIGURATION_FAULT' });
  assert.deepEqual(calls, ['home_machine']);
  for (const state of ['accepted', 'succeeded']) {
    const run = () => dispatchMutationOnce(async name => {
      if (name === 'request_control_session') throw Object.assign(new Error('lost'), { code: 'MCP_TRANSPORT_ERROR' });
      return { found: true, session_receipt: { session_id: 'session', state } };
    }, 'request_control_session', { request_id: 'grant' });
    if (state === 'accepted') await assert.rejects(run, { code: 'OUTCOME_UNKNOWN' }); else assert.equal((await run()).session_id, 'session');
  }
});

test('bounded numeric summaries retain count/extrema/net change without keeping sample arrays', () => {
  const summary = new SampleSummary(); for (let i = 0; i < 100000; i++) summary.add({ heap: i, rss: 200 });
  assert.equal(summary.snapshot().fields.heap.net_change, 99999);
  assert.equal(summary.snapshot().fields.heap.mean, 49999.5);
  assert.ok(JSON.stringify(summary).length < 400);
});

test('local ps parsing checks java identity, start time and RSS without accepting arbitrary command text', () => {
  assert.deepEqual(parseNativeProcess(' 123456 Thu Sep 10 14:15:16 2026 /opt/jdk/bin/java\n'), {
    rss_bytes: 123456 * 1024, started_at_ps: 'Thu Sep 10 14:15:16 2026', executable: 'java',
  });
  assert.throws(() => parseNativeProcess(' 120 Thu Sep 10 14:15:16 2026 /usr/bin/node'));
  assert.throws(() => parseNativeProcess('garbage'));
});

test('paired latency statistics report actual nearest-rank p95 and peak', () => {
  const result = latencyStats(Array.from({ length: 20 }, (_, index) => index + 1));
  assert.equal(result.p95_ms, 19); assert.equal(result.peak_ms, 20); assert.equal(result.mean_ms, 10.5);
  assert.throws(() => latencyStats([])); assert.throws(() => latencyStats([Infinity]));
});

test('completed smoke, elapsed time alone and incomplete evidence can never qualify the simulator soak or full A11', () => {
  const criteria = { smoke: false, elapsedMs: 8 * 3600000, placements: 10000,
    completed: { state: 'succeeded', result: { requested: 10000, placed: 10000 } }, inventory: { feed_count: 10000, remaining: 0 },
    resyncCount: 1, ambientCycles: 100, failure: null, interrupted: false, rssMeasured: true, baselineMeasured: true };
  const result = qualifyResult(criteria); assert.equal(result.simulator_soak_complete, true); assert.equal(result.full_A11_qualified, false); assert.equal(result.hardware_qualified, false);
  for (const change of [{ smoke: true }, { elapsedMs: 7.99 * 3600000 }, { completed: null }, { inventory: { feed_count: 9999, remaining: 1 } },
    { resyncCount: 0 }, { ambientCycles: 0 }, { failure: { code: 'OUTCOME_UNKNOWN' } }, { interrupted: true }]) {
    assert.equal(qualifyResult({ ...criteria, ...change }).simulator_soak_complete, false);
  }
});

test('actual SDK disconnected run writes failed durable evidence, never qualifies, and refuses to overwrite that run', { timeout: 30000 }, async t => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), 'openpnp-soak-harness-')); t.after(() => rm(temporary, { recursive: true, force: true }));
  const options = parseOptions(['--connection-file', path.join(temporary, 'absent-connection.json'), '--evidence-dir', path.join(temporary, 'evidence'), '--duration-hours', '0.001', '--placements', '1']);
  const result = await runSoak(options);
  assert.equal(result.status, 'failed'); assert.equal(result.hardware_qualified, false); assert.equal(result.simulator_soak_complete, false);
  const persisted = JSON.parse(await readFile(path.join(options.evidenceDir, 'report.json'), 'utf8'));
  assert.equal(persisted.failure.code, 'INVALID_CONNECTION');
  assert.equal(persisted.mcp_snapshot_unchanged_after_run, true);
  assert.equal(persisted.mcp_runtime_snapshot.execution_path, '<evidence-dir>/mcp-runtime/server.mjs');
  assert.equal(persisted.mcp_runtime_snapshot.server_sha256, persisted.bundled_mcp_sha256);
  assert.ok(persisted.mcp_runtime_snapshot.files.some(file => file.path === 'domain/index.mjs'));
  const progress = (await readFile(path.join(options.evidenceDir, 'progress.jsonl'), 'utf8')).trim().split('\n').map(line => JSON.parse(line));
  assert.ok(progress.some(entry => entry.type === 'run_error'));
  assert.equal(progress.filter(entry => entry.type === 'mutation_intent').length, 0);
  assert.deepEqual(progress.map(entry => entry.sequence), progress.map((_, index) => index + 1));
  await assert.rejects(() => runSoak(options), { code: 'EEXIST' });
});

test('one exact job disposition read preserves known failure/unknown/success when details are unavailable', async () => {
 const { readJobDisposition } = await import('../../scripts/openpnp-soak.mjs');
 for (const state of ['succeeded', 'failed', 'outcome_unknown', 'paused']) {
  const observed = { operation_id: 'original-op', request_id: 'original-request', state, result: { requested: 200, placed: 42, code: 'KNOWN_NATIVE_ERROR' } };
  let calls = 0;
  const lost = await readJobDisposition(async (name,args) => { calls++; assert.equal(name,'get_operation'); assert.deepEqual(args,{operation_id:'original-op',view:'full'}); throw Object.assign(new Error('store full'),{code:'RESPONSE_DETAILS_UNAVAILABLE'}); }, observed);
  assert.equal(calls,1); assert.equal(lost.observed,observed); assert.equal(lost.details_error.code,'RESPONSE_DETAILS_UNAVAILABLE'); assert.equal(lost.details_available,false);
  const known = await readJobDisposition(async () => structuredClone(observed), observed); assert.deepEqual(known.full,observed);
  const mismatch = await readJobDisposition(async () => ({...observed,state:'running'}),observed); assert.equal(mismatch.details_available,false); assert.equal(mismatch.observed.state,state);
 }
});
