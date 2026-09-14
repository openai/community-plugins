// SPDX-License-Identifier: Apache-2.0
// Real packaged stdio/official SDK/HTTP transport; deliberately synthetic native endpoint.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, createHash, randomBytes } from 'node:crypto';
import http from 'node:http';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile, writeFile, mkdir, mkdtemp } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { TOOL_DEFINITIONS, publicDefinition } from '../../src/openpnp/node/contracts.mjs';
import { PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { openPackagedRecovery, requestRecovery, observeRecovery } from './native-sensing-restart-packaged-mcp.mjs';
const root = fileURLToPath(new URL('../../', import.meta.url));
const requestTool = 'openpnp_request_sensing_reconciliation', readTool = 'openpnp_get_sensing_reconciliation';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const base = attempt => ({ session_id: 'protocol-fixture-session', request_id: randomUUID(), expected_config_revision: 'cfg-4', recovery_kind: 'restart-faulted-job-replacement', replacement_attempt_id: attempt });

test('isolated packaged recovery transport keeps local restart decisions separate from MCP', async t => {
  const suppliedState = process.env.OPENPNP_PACKAGED_RESTART_TEST_STATE;
  const parent = path.join(root, 'validation');
  if (!suppliedState) await mkdir(parent, { recursive: true, mode: 0o700 });
  const state = suppliedState || await mkdtemp(path.join(parent, 'packaged-restart-contract-'));
  if (suppliedState) await mkdir(state, { mode: 0o700 });
  t.diagnostic(`Retained isolated protocol fixture: ${state}`);
  const calls = [], tasks = new Map(), operations = new Map(), byRequest = new Map(), attempt = randomUUID();
  let connection, cleanup, malformed = false, fixtureError;
  const capabilities = { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM,
    machine_id: 'protocol-fixture-machine', bridge_instance_id: randomUUID(), simulator_profile: 'gui-simulator',
    tools: ['openpnp_get_capabilities', 'openpnp_get_operation', 'openpnp_get_request_status', requestTool, readTool],
    vacuum_sensing: { available: false }, sensing_reconciliation: { profile: 'native-simulator-sensing-reconciliation-v1',
      available: true, restart_request_available: true, simulation_only: true, hardware_qualified: false } };
  const token = randomBytes(32).toString('base64url');
  const server = http.createServer(async (request, response) => {
    try {
      assert.equal(request.url, '/rpc'); assert.equal(request.method, 'POST');
      assert.equal(request.headers.authorization, `Bearer ${token}`);
      let body = ''; for await (const chunk of request) body += chunk;
      assert.ok(body.length < 65536);
      const { method, params } = JSON.parse(body); calls.push({ method, params });
      let result;
      if (method === 'openpnp_get_capabilities') result = capabilities;
      else if (method === requestTool) {
        if (byRequest.has(params.request_id)) result = byRequest.get(params.request_id);
        else {
          const taskId = randomUUID(), operationId = randomUUID();
          result = { operation_id: operationId, request_id: params.request_id, config_revision: params.expected_config_revision,
            state: 'succeeded', result: { task_id: taskId } };
          const context = params.recovery_kind === 'restart-faulted-job-replacement' ? 'restart_context' : 'replacement_context';
          tasks.set(taskId, { task: { task_id: taskId, recovery_kind: params.recovery_kind, [context]: { replacement_attempt_id: attempt } },
            state: 'awaiting_local_decision', task_active: true, execution_authority_restored: false, live_resolution_activated: false,
            receipt: null, fixture_marker: 'synthetic endpoint; no native observations' });
          operations.set(operationId, result); byRequest.set(params.request_id, result);
        }
        if (malformed) result = { ...result, request_id: randomUUID() };
      } else if (method === 'openpnp_get_operation') result = operations.get(params.operation_id);
      else if (method === 'openpnp_get_request_status') result = { found: true, operation: byRequest.get(params.request_id) };
      else if (method === readTool) result = tasks.get(params.task_id);
      else throw new Error(`Unexpected HTTP dispatch: ${method}`);
      assert.ok(result); response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ result }));
    } catch (error) { fixtureError = String(error); response.statusCode = 500; response.end('{}'); }
  });
  t.after(async () => {
    try { cleanup = await connection?.close(); } finally { await new Promise(resolve => server.close(resolve)); }
    await writeFile(path.join(state, 'transport-proof.json'), JSON.stringify({ test_outcome_source: 'Authoritative Node test process exit and TAP log; fixture data is not native evidence',
      scope: 'Real sparse bundled MCP, SDK1.30 and loopback HTTP contract fixture; native states and phase changes synthetic',
      native_qualification: false, gui_qualification: false, hardware_qualification: false, server_sha256: hash(await readFile(path.join(root, 'plugins/openpnp/mcp/server.mjs'))),
      official_sdk_version: JSON.parse(await readFile(path.join(root, 'src/openpnp/node/node_modules/@modelcontextprotocol/sdk/package.json'))).version,
      node: process.version, cleanup, fixture_error: fixtureError, http_calls: calls, mcp_transcript: connection?.transcript,
    }, (key, value) => key === 'session_id' ? '<protocol-fixture-session>' : value, 2) + '\n', { flag: 'wx', mode: 0o600 });
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const tokenFile = path.join(state, 'token'), connectionFile = path.join(state, 'connection.json');
  await writeFile(tokenFile, token, { flag: 'wx', mode: 0o600 });
  await writeFile(connectionFile, JSON.stringify({ schemaVersion: 1, url: `http://127.0.0.1:${server.address().port}`, tokenFile, machineId: capabilities.machine_id }), { flag: 'wx', mode: 0o600 });
  const packaged = path.join(root, 'plugins/openpnp/mcp/server.mjs');
  connection = await openPackagedRecovery({ server: packaged, server_sha256: hash(await readFile(packaged)), state: path.join(state, 'mcp'), connection_file: connectionFile });

  await t.test('catalog matches current source and typed invalid requests never reach HTTP', async () => {
    const catalog = await connection.client.listTools();
    assert.deepEqual(catalog.tools, TOOL_DEFINITIONS.map(publicDefinition));
    assert.deepEqual(JSON.parse(await readFile(path.join(root, 'plugins/openpnp/mcp/tool-inputs.json'))).tools, catalog.tools);
    for (const name of ['openpnp_submit_restart_observations', 'openpnp_submit_sensing_reconciliation', 'openpnp_clear_sensing_fault']) assert.ok(!catalog.tools.some(tool => tool.name === name));
    const input = base(attempt), before = calls.length;
    const invalid = Object.keys(input).map(key => { const row = { ...input }; delete row[key]; return row; });
    for (const key of ['job_id', 'original_operation_id', 'expected_job_revision', 'expected_board_load_revision', 'expected_material_revision', 'observations', 'local_authority', 'source']) invalid.push({ ...input, [key]: randomUUID() });
    for (const value of ['bad', 'AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA']) invalid.push({ ...input, replacement_attempt_id: value });
    for (const args of invalid) { const response = await connection.raw(requestTool, args); assert.equal(response.isError, true); assert.equal(response.structuredContent.error.code, 'INVALID_ARGUMENT'); }
    assert.equal(calls.length, before);
  });
  await t.test('explicit restart capability is enforced at runtime even when static catalog contains request', async () => {
    const recovery = capabilities.sensing_reconciliation;
    for (const value of [undefined, false, 'true']) {
      recovery.restart_request_available = value; const before = calls.filter(row => row.method === requestTool).length;
      const response = await connection.raw(requestTool, base(attempt));
      assert.equal(response.structuredContent.error.code, 'UNSUPPORTED_CAPABILITY');
      assert.equal(calls.filter(row => row.method === requestTool).length, before);
    }
    recovery.restart_request_available = true;
  });
  let restart;
  await t.test('source-absent request forwards exact five fields and only presents pending local task', async () => {
    const input = base(attempt); restart = await requestRecovery(connection, input);
    assert.equal(restart.local_decision_submitted, false); assert.equal(restart.task.state, 'awaiting_local_decision');
    assert.deepEqual(calls.filter(row => row.method === requestTool).at(-1).params, input);
    const duplicate = await connection.call(requestTool, input); assert.equal(duplicate.operation_id, restart.operation.operation_id);
    assert.equal(tasks.size, 1);
  });
  await t.test('paged observation completion remains exact and triggers no continuation or job execution', async () => {
    // State advancement is controlled test data, never a simulated native callback or receipt claim.
    const completed = tasks.get(restart.task.task.task_id);
    const localOperation = randomUUID();
    operations.set(localOperation, { operation_id: localOperation, state: 'succeeded', native_completion: { native_wrapper_completed: true, native_wrapper_succeeded: true } });
    Object.assign(completed, { state: 'restart_observations_completed', task_active: false, recovery_operation_id: localOperation,
      receipt: { faults_resolved: false, execution_authority_restored: false, recovery_operation_id: localOperation, native_wrapper_completed: true, native_wrapper_succeeded: true, observations: Array.from({ length: 1000 }, (_, n) => ({ receipt_id: randomUUID(), index: n, fixture_only: true })) } });
    const before = calls.filter(row => row.method === requestTool).length;
    const observed = await observeRecovery(connection, { task_id: completed.task.task_id, replacement_attempt_id: attempt, recovery_kind: 'restart-faulted-job-replacement' });
    assert.deepEqual(observed.task, completed); assert.equal(observed.continuation_requested, false); assert.equal(observed.job_started, false);
    completed.receipt.native_wrapper_succeeded = false;
    await assert.rejects(observeRecovery(connection, { task_id: completed.task.task_id, replacement_attempt_id: attempt, recovery_kind: 'restart-faulted-job-replacement' }));
    completed.receipt.native_wrapper_succeeded = true;
    operations.get(localOperation).native_completion.native_wrapper_completed = false;
    await assert.rejects(observeRecovery(connection, { task_id: completed.task.task_id, replacement_attempt_id: attempt, recovery_kind: 'restart-faulted-job-replacement' }));
    operations.get(localOperation).native_completion.native_wrapper_completed = true;
    assert.equal(calls.filter(row => row.method === requestTool).length, before);
    assert.ok(connection.transcript.some(row => row.name === 'openpnp_read_response_page'));
    assert.ok(!calls.some(row => row.method === 'openpnp_read_response_page'), 'Retained pages do not reread native state');
    capabilities.vacuum_sensing.available = true; capabilities.sensing_reconciliation.restart_request_available = false;
    const continuation = await requestRecovery(connection, { ...base(attempt), recovery_kind: 'continue-faulted-job-replacement' });
    assert.notEqual(continuation.task.task.task_id, completed.task.task_id); assert.equal(continuation.task.state, 'awaiting_local_decision');
    assert.deepEqual(tasks.get(completed.task.task_id), completed);
  });
  await t.test('lost response binding is outcome-unknown and lookup does not repeat the accepted request', async () => {
    const input = { ...base(attempt), recovery_kind: 'continue-faulted-job-replacement' }; malformed = true;
    const response = await connection.raw(requestTool, input); malformed = false;
    assert.equal(response.isError, true); assert.equal(response.structuredContent.error.code, 'OUTCOME_UNKNOWN');
    assert.equal(response.structuredContent.error.details.request_id, input.request_id);
    const lookup = await connection.call('openpnp_get_request_status', { request_id: input.request_id });
    assert.equal(lookup.operation.request_id, input.request_id);
    assert.equal(calls.filter(row => row.method === requestTool && row.params.request_id === input.request_id).length, 1);
  });
  await t.test('standalone prepared driver observes completion without submitting a local decision', async () => {
    const configFile = path.join(state, 'driver-config.json'), output = path.join(state, 'driver-proof.json');
    await writeFile(configFile, JSON.stringify({ action: 'observe', server: packaged, server_sha256: hash(await readFile(packaged)),
      state: path.join(state, 'driver-mcp'), connection_file: connectionFile, output,
      input: { task_id: restart.task.task.task_id, replacement_attempt_id: attempt, recovery_kind: 'restart-faulted-job-replacement' } }), { flag: 'wx', mode: 0o600 });
    const before = calls.filter(row => row.method === requestTool).length;
    const executed = await promisify(execFile)(process.execPath, [path.join(root, 'tests/openpnp/native-sensing-restart-packaged-mcp.mjs'), configFile], { timeout: 15000 });
    const proof = JSON.parse(await readFile(output)); assert.equal(proof.passed, true); assert.equal(proof.result.task.state, 'restart_observations_completed');
    assert.equal(proof.result.continuation_requested, false); assert.equal(proof.cleanup.server_gone, true);
    assert.equal(calls.filter(row => row.method === requestTool).length, before);
    assert.equal(JSON.parse(executed.stdout.trim()).passed, true);
  });
  await t.test('historical reads survive source and authority removal and do not reopen tasks', async () => {
    capabilities.tools = ['openpnp_get_capabilities', readTool]; capabilities.vacuum_sensing.available = false;
    capabilities.sensing_reconciliation.available = false; capabilities.sensing_reconciliation.restart_request_available = false;
    const retained = tasks.get(restart.task.task.task_id); retained.historical = true;
    const before = tasks.size;
    const history = await connection.call(readTool, { task_id: retained.task.task_id });
    assert.deepEqual(history, retained); assert.equal(history.execution_authority_restored, false);
    const rejected = await connection.raw(requestTool, base(attempt)); assert.equal(rejected.structuredContent.error.code, 'UNSUPPORTED_CAPABILITY');
    assert.equal(tasks.size, before); assert.ok(calls.every(row => !/submit|start_job|validate_job|set_machine_enabled/.test(row.method)));
  });
  assert.equal(fixtureError, undefined);
});
