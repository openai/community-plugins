// SPDX-License-Identifier: Apache-2.0
// Request/read-only driver for an independently owned native GUI restart fixture.
// Local attestations and decisions are deliberately outside this MCP process.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile, realpath } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const requestTool = 'openpnp_request_sensing_reconciliation';
const readTool = 'openpnp_get_sensing_reconciliation';
const alive = pid => { try { process.kill(pid, 0); return true; } catch (error) { if (error.code === 'ESRCH') return false; throw error; } };

export async function openPackagedRecovery(options) {
  assert.equal(sha(await readFile(options.server)), options.server_sha256, 'Exact isolated packaged server');
  await mkdir(options.state, { mode: 0o700 });
  const transcript = [], transport = new StdioClientTransport({ command: process.execPath, args: [options.server, '--stdio'], cwd: options.state,
    env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: options.state, OPENPNP_CONNECTION_FILE: options.connection_file }, stderr: 'pipe' });
  const client = new Client({ name: 'openpnp-restart-packaged', version: '1' });
  let stderr = '';
  transport.stderr?.on('data', chunk => { stderr = (stderr + chunk).slice(-16384); });
  await client.connect(transport);
  const serverPid = transport.pid;
  assert.ok(Number.isInteger(serverPid));
  const raw = async (name, args = {}) => {
    const response = await client.callTool({ name, arguments: args }, undefined, { timeout: 5000 });
    transcript.push({ name, args: structuredClone(args), response }); return response;
  };
  const call = async (name, args = {}) => {
    const result = await raw(name, args);
    assert.notEqual(result.isError, true, JSON.stringify(result.structuredContent));
    return readCompleteResponse(result.structuredContent, page => call('openpnp_read_response_page', page));
  };
  return { client, raw, call, transcript, serverPid, async close() {
    await client.close();
    const end = Date.now() + 5000; while (alive(serverPid) && Date.now() < end) await delay(20);
    assert.equal(alive(serverPid), false, 'Only owned packaged MCP child is closed/reaped');
    return { server_pid: serverPid, server_gone: true, stderr };
  } };
}

export async function requestRecovery(connection, input) {
  assert.ok(['restart-faulted-job-replacement', 'continue-faulted-job-replacement'].includes(input.recovery_kind));
  const caps = await connection.call('openpnp_get_capabilities');
  assert.equal(caps.connected, true);
  assert.ok(caps.tools.includes(requestTool));
  const recovery = caps.bridge.sensing_reconciliation;
  assert.equal(recovery.profile, 'native-simulator-sensing-reconciliation-v1');
  assert.equal(recovery.available, true); assert.equal(recovery.simulation_only, true); assert.equal(recovery.hardware_qualified, false);
  if (input.recovery_kind === 'restart-faulted-job-replacement') {
    assert.equal(recovery.restart_request_available, true);
    assert.equal(caps.bridge.vacuum_sensing.available, false, 'Restart request begins without a live source');
  }
  let operation = await connection.call(requestTool, input);
  const operationId = operation.operation_id;
  const end = Date.now() + 15000;
  while (['accepted', 'running'].includes(operation.state)) {
    assert.ok(Date.now() < end, 'Bounded request lookup; never repeat unknown request');
    await delay(30); operation = await connection.call('openpnp_get_operation', { operation_id: operationId });
  }
  assert.equal(operation.state, 'succeeded'); assert.equal(operation.operation_id, operationId);
  const task = await connection.call(readTool, { task_id: operation.result.task_id });
  assert.equal(task.task.task_id, operation.result.task_id);
  assert.equal(task.task.recovery_kind, input.recovery_kind); assert.equal(task.task_active, true);
  const context = input.recovery_kind === 'restart-faulted-job-replacement' ? task.task.restart_context : task.task.replacement_context;
  assert.equal(context.replacement_attempt_id, input.replacement_attempt_id);
  return { capabilities: caps, operation, task, local_decision_submitted: false };
}

export async function observeRecovery(connection, { task_id, replacement_attempt_id, recovery_kind }) {
  const task = await connection.call(readTool, { task_id });
  assert.equal(task.task.task_id, task_id); assert.equal(task.task.recovery_kind, recovery_kind);
  const context = recovery_kind === 'restart-faulted-job-replacement' ? task.task.restart_context : task.task.replacement_context;
  assert.equal(context.replacement_attempt_id, replacement_attempt_id);
  if (task.state === 'restart_observations_completed') {
    assert.equal(recovery_kind, 'restart-faulted-job-replacement');
    assert.equal(task.execution_authority_restored, false); assert.equal(task.live_resolution_activated, false);
    assert.equal(task.receipt.faults_resolved, false); assert.equal(task.receipt.execution_authority_restored, false);
    assert.equal(task.task_active, false);
    assert.equal(task.receipt.native_wrapper_completed, true); assert.equal(task.receipt.native_wrapper_succeeded, true);
    assert.equal(task.recovery_operation_id, task.receipt.recovery_operation_id);
    const operation = await connection.call('openpnp_get_operation', { operation_id: task.recovery_operation_id });
    assert.equal(operation.operation_id, task.recovery_operation_id); assert.equal(operation.state, 'succeeded');
    assert.equal(operation.native_completion.native_wrapper_completed, true); assert.equal(operation.native_completion.native_wrapper_succeeded, true);
  }
  // Report state only. The independently owned GUI test checks its actual native wrapper,
  // source/load receipts and separate continuation; these reported wrapper facts never upgrade readiness.
  return { task, local_decision_submitted: false, continuation_requested: false, job_started: false };
}

let isMain = false;
try { isMain = import.meta.url === pathToFileURL(await realpath(process.argv[1])).href; } catch {}
if (isMain) {
  const config = JSON.parse(await readFile(process.argv[2], 'utf8'));
  let connection, result, failure, cleanup;
  try {
    assert.ok(['request', 'observe'].includes(config.action));
    connection = await openPackagedRecovery(config);
    result = config.action === 'request' ? await requestRecovery(connection, config.input) : await observeRecovery(connection, config.input);
  } catch (error) { failure = { name: error.name, message: error.message }; }
  finally { try { cleanup = await connection?.close(); } catch (error) { failure ??= { name: error.name, message: error.message }; } }
  const evidence = { passed: !failure, action: config.action, result, failure, cleanup, transcript: connection?.transcript,
    server_sha256: config.server_sha256, driver_sha256: sha(await readFile(new URL(import.meta.url))), node: process.version,
    scope: 'Official SDK and packaged stdio request/read only; external native fixture supplies local GUI decisions and native qualification',
    observation_submission_over_mcp: false, physical_qualification: false };
  await writeFile(config.output, JSON.stringify(evidence, (key, value) => key === 'session_id' ? '<owned-session>' : value, 2) + '\n', { flag: 'wx', mode: 0o600 });
  console.log(JSON.stringify({ passed: !failure, action: config.action, server_pid: cleanup?.server_pid, task_id: result?.task?.task?.task_id, state: result?.task?.state }));
  if (failure) process.exitCode = 1;
}
