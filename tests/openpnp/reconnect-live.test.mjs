// Executes real native OpenPnP only with an explicitly selected sustained simulator.
import test from 'node:test';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { mkdtemp, rm, readFile, mkdir, writeFile, cp, readdir, stat } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { inventoryFor, makeWorkloadCsv, PINNED_UPSTREAM } from '../../scripts/openpnp-soak.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const connection = process.env.OPENPNP_E2E_RECOVERY_CONNECTION_FILE;
test('MCP disconnect and expired supervision pause native work; a new lease resumes the original operation without duplicate feeds',
  { skip: !connection, timeout: 180000 }, async t => {
    const state = await mkdtemp(path.join(os.tmpdir(), 'openpnp-reconnect-live-'));
    const evidenceDir = process.env.OPENPNP_E2E_EVIDENCE_DIR;
    const failures = []; let passed = false, progressPolls = 0, fullOperationReads = 0;
    const mcp = path.join(state, 'mcp');
    await cp(path.join(root, 'plugins/openpnp/mcp'), mcp, { recursive: true });
    const bundleHash = createHash('sha256').update(await readFile(path.join(mcp, 'server.mjs'))).digest('hex');
    const connectionHashes = [];
    let client, session;
    async function connect() {
      assert.equal(createHash('sha256').update(await readFile(path.join(mcp, 'server.mjs'))).digest('hex'), bundleHash);
      const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(mcp, 'server.mjs'), '--stdio'],
        cwd: state, env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: connection }, stderr: 'pipe' });
      client = new Client({ name: 'openpnp-reconnect-e2e', version: '1.0.0' }); await client.connect(transport);
      connectionHashes.push(bundleHash);
    }
    async function call(name, args = {}) {
      let response;
      try {
        response = await client.callTool({ name: `openpnp_${name}`, arguments: args });
        assert.notEqual(response.isError, true, JSON.stringify(response.structuredContent));
        if (args.view === 'progress') {
          progressPolls++; assert.equal(response.structuredContent.response_view?.name, 'progress');
          assert.equal(response.structuredContent.response_view.retained_snapshot, false);
          assert.notEqual(response.structuredContent.truncated, true);
        }
        return await readCompleteResponse(response.structuredContent, pageArgs => call('read_response_page', pageArgs));
      } catch (error) {
        const known = error.known_response ?? response?.structuredContent;
        if (failures.length < 16) failures.push({ tool: `openpnp_${name}`, view: args.view ?? 'full', operation_id: args.operation_id ?? null, request_id: args.request_id ?? null,
          error: { code: error.code ?? 'ASSERTION_OR_TRANSPORT_FAILURE', message: error.message },
          storage_error: known?.response_retention?.storage_error ?? null, known_response: known ?? null, native_action_repeated: false });
        throw error;
      }
    }
    t.after(async () => {
      if (client && session) { try { await call('release_control_session', { session_id: session }); } catch {} }
      if (client) await client.close();
      if (!passed) {
        // Failed response retention is evidence: preserve this exact MCP store,
        // including earlier receipts, rather than deleting it in test cleanup.
        if (evidenceDir) {
          await mkdir(evidenceDir, { recursive: true, mode: 0o700 });
          const retainedState = path.join(evidenceDir, 'failed-mcp-state');
          await cp(state, retainedState, { recursive: true, errorOnExist: true, force: false });
          await writeFile(path.join(evidenceDir, 'response-failures.json'), JSON.stringify({ failed: true, failures, progress_polls: progressPolls, full_operation_reads: fullOperationReads, retained_mcp_state: 'failed-mcp-state', native_action_replays: 0 }, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
          await rm(state, { recursive: true, force: true });
        } else t.diagnostic(`Failed MCP state retained at ${state}`);
      } else await rm(state, { recursive: true, force: true });
    });
    async function observe(handle, desired = 'succeeded') {
      const deadline = Date.now() + 90000; let receipt = handle;
      while (['accepted', 'running'].includes(receipt.state)) {
        assert.ok(Date.now() < deadline, 'Original native operation timed out; no replay was sent.');
        await delay(100); receipt = await call('get_operation', { operation_id: receipt.operation_id, view: 'progress' });
      }
      assert.equal(receipt.state, desired, JSON.stringify(receipt));
      // Retain one complete receipt after the selected stable transition. Progress
      // deliberately does not claim to be an exact archived native snapshot.
      fullOperationReads++;
      receipt = await call('get_operation', { operation_id: receipt.operation_id, view: 'full' });
      assert.equal(receipt.state, desired, JSON.stringify(receipt));
      const idleDeadline = Date.now() + 10000;
      while ((await call('get_status', { view: 'progress' })).native_busy) {
        assert.ok(Date.now() < idleDeadline, 'Native executor remained busy after its operation receipt; no command was replayed.');
        await delay(50);
      }
      return receipt;
    }
    async function operation(name, args = {}) {
      return observe(await call(name, { request_id: randomUUID(), session_id: session, ...args }));
    }
    await connect();
    const capabilities = await call('get_capabilities');
    assert.equal(capabilities.connected, true); assert.equal(capabilities.bridge.upstream_commit, PINNED_UPSTREAM);
    assert.equal(capabilities.bridge.simulation, true); assert.equal(capabilities.bridge.hardware_qualified, false);
    assert.equal(capabilities.bridge.simulator_profile, 'sustained-workload');
    const initial = await call('get_status'); assert.ok(initial.active_operation_id == null);
    const part = initial.machine.parts.find(item => item.id === 'R0603-1K');
    const inventoryBefore = inventoryFor(initial.machine, part.id); assert.ok(inventoryBefore.remaining >= 200);
    session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 600 })).session_id;
    await operation('set_machine_enabled', { enabled: true }); await operation('home_machine');
    const imported = await call('import_job', { format: 'reference-csv', content: makeWorkloadCsv(part, 200), units: 'mm', widthMm: 150, heightMm: 150 });
    const prepared = await operation('prepare_job', { artifact_id: imported.artifact_id });
    assert.equal((await operation('validate_job')).result.valid, true);
    await call('renew_control_session', { session_id: session, ttl_seconds: 10 });
    const requestId = randomUUID();
    const started = await call('start_job', { request_id: requestId, session_id: session, job_id: prepared.result.job_id });
    // Let actual native work begin, then remove the MCP server/client completely.
    let running = started;
    for (let attempt = 0; attempt < 30 && !(running.native_steps_started > 1); attempt++) {
      await delay(100); running = await call('get_operation', { operation_id: started.operation_id, view: 'progress' });
    }
    assert.equal(running.state, 'running'); assert.ok(running.native_steps_started > 1);
    await client.close(); client = null;
    await delay(12000);
    await connect();
    const reconnected = await call('get_status');
    assert.equal(reconnected.machine_id, initial.machine_id);
    assert.equal(reconnected.bridge_instance_id, initial.bridge_instance_id, 'The JVM must remain the same while the MCP processes disconnect.');
    const lookup = await call('get_request_status', { request_id: requestId, view: 'progress' });
    assert.equal(lookup.found, true); assert.equal(lookup.operation.operation_id, started.operation_id);
    const paused = await observe(lookup.operation, 'paused');
    assert.ok(paused.result.placed < 200); assert.ok(paused.result.placed >= 0);
    const atPause = inventoryFor((await call('get_status')).machine, part.id);
    await delay(1200);
    assert.deepEqual(inventoryFor((await call('get_status')).machine, part.id), atPause, 'Expired ownership must stop further feeding.');
    const expiredSession = session;
    const expiredRenewal = await client.callTool({ name: 'openpnp_renew_control_session', arguments: { session_id: expiredSession, ttl_seconds: 600 } });
    assert.equal(expiredRenewal.isError, true);
    assert.equal(expiredRenewal.structuredContent.error.code, 'SESSION_REQUIRED');
    session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 600 })).session_id;
    assert.notEqual(session, expiredSession);
    const resumed = await call('resume_job', { request_id: randomUUID(), session_id: session, operation_id: started.operation_id });
    assert.equal(resumed.operation_id, started.operation_id);
    const completed = await observe(resumed);
    assert.equal(completed.result.placed, 200); assert.equal(completed.result.requested, 200);
    assert.equal(completed.result.independently_inspected, 0);
    const inventoryAfter = inventoryFor((await call('get_status')).machine, part.id);
    assert.equal(inventoryAfter.feed_count - inventoryBefore.feed_count, 200, 'Resume must retain native feeder and placed history.');
    await operation('set_machine_enabled', { enabled: false });
    await call('release_control_session', { session_id: session }); session = null;
    if (evidenceDir) {
      const dir = evidenceDir; await mkdir(dir, { recursive: true, mode: 0o700 });
      const responseFiles = await readdir(path.join(state, 'responses')).catch(error => { if (error.code === 'ENOENT') return []; throw error; });
      const responseBytes = (await Promise.all(responseFiles.map(async file => (await stat(path.join(state, 'responses', file))).size))).reduce((sum, size) => sum + size, 0);
      await writeFile(path.join(dir, 'mcp-native-reconnect.json'), JSON.stringify({
        schema_version: 1, completed_at: new Date().toISOString(), passed: true, simulation_only: true, hardware_qualified: false,
        node: process.versions.node, upstream_commit: PINNED_UPSTREAM, bridge_artifact_sha256: capabilities.bridge.bridge_artifact_sha256 ?? null,
        packaged_server_sha256: bundleHash, mcp_connection_bundle_hashes: connectionHashes,
        machine_id: initial.machine_id, bridge_instance_id: initial.bridge_instance_id, same_native_instance_after_reconnect: true,
        original_operation_id: started.operation_id, original_request_id: requestId, disconnect_wait_ms: 12000, lease_ttl_seconds: 10,
        native_steps_started_before_disconnect: running.native_steps_started, placed_at_pause: paused.result.placed,
        inventory_before: inventoryBefore, inventory_paused: atPause, inventory_after: inventoryAfter,
        final_placed: 200, independently_inspected: 0, mutation_replays: 0,
        progress_polls: progressPolls, full_operation_reads: fullOperationReads, retained_response_files: responseFiles.length, retained_response_bytes: responseBytes,
        polling_scope: 'Explicit progress views for repeated polls; complete native receipts fetched once per observed pause/terminal transition. Full native journal unchanged.',
        expired_session_renewal_rejected: true,
        qualification_scope: 'Actual MCP transport disconnect, native lease expiry, cooperative pause and original-operation resume; no JVM/controller crash was injected.',
      }, null, 2) + '\n');
    }
    passed = true;
  });
