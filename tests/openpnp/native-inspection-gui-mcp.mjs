// Actual packaged stdio MCP client for the owned native GUI test. No observation submission API.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const args = JSON.parse(await readFile(process.argv[2], 'utf8'));
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const server = path.join(root, 'plugins/openpnp/mcp/server.mjs');
const evidence = { passed: false, node: process.version, checks: [], tool_calls: [],
  mcp_server_sha256: sha(await readFile(server)), helper_sha256: sha(await readFile(fileURLToPath(import.meta.url))),
  official_sdk_version: JSON.parse(await readFile(path.join(root, 'src/openpnp/node/node_modules/@modelcontextprotocol/sdk/package.json'), 'utf8')).version,
  simulation_only: true, physical_qualification: false, observation_submission_over_mcp: false };
const check = (condition, label) => { assert.ok(condition, label); evidence.checks.push(label); };
const alive = pid => { try { process.kill(pid, 0); return true; } catch (error) { if (error.code === 'ESRCH') return false; throw error; } };
let client, transport, serverPid, failure;
try {
  check(Number(process.versions.node.split('.')[0]) >= 22, 'Node 22 or later runs the official SDK');
  check(evidence.mcp_server_sha256 === args.mcp_server_sha256, 'Exact packaged MCP server hash matches the Java fixture');
  await mkdir(args.mcp_state, { mode: 0o700 });
  transport = new StdioClientTransport({ command: process.execPath, args: [server, '--stdio'], cwd: args.mcp_state,
    env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: args.mcp_state, OPENPNP_CONNECTION_FILE: args.connection_file }, stderr: 'pipe' });
  client = new Client({ name: 'native-inspection-gui-verification', version: '1' });
  let stderr = '';
  transport.stderr?.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-16384); });
  await client.connect(transport); serverPid = transport.pid;
  check(Number.isInteger(serverPid) && serverPid > 0, 'Official SDK owns a separate packaged MCP process');
  const raw = async (name, arguments_ = {}) => {
    const result = await client.callTool({ name, arguments: arguments_ }, undefined, { timeout: 5000 });
    evidence.tool_calls.push({ name, is_error: result.isError === true, error_code: result.structuredContent?.error?.code,
      operation_id: result.structuredContent?.operation_id, task_id: result.structuredContent?.task_id });
    return result;
  };
  const call = async (name, arguments_ = {}) => {
    const result = await raw(name, arguments_);
    assert.notEqual(result.isError, true, JSON.stringify(result.structuredContent));
    return readCompleteResponse(result.structuredContent, page => call('openpnp_read_response_page', page));
  };
  const catalog = await client.listTools();
  const names = catalog.tools.map(tool => tool.name);
  check(names.length === 61 && new Set(names).size === 61, 'Packaged catalog exposes exactly 61 distinct typed tools');
  check(names.includes('openpnp_request_board_inspection') && names.includes('openpnp_get_board_inspection'), 'Both native inspection request and read tools are packaged');
  check(!names.includes('openpnp_submit_board_inspection') && !names.includes('local_native_inspection_submission'), 'Catalog exposes no native local observation callback');
  evidence.catalog_count = names.length;
  const caps = await call('openpnp_get_capabilities');
  check(caps.connected === true && caps.bridge.simulator_profile === 'gui-simulator', 'MCP is connected to the actual GUI-owned simulator');
  check(caps.bridge.bridge_artifact_sha256 === args.bridge_sha256, 'MCP observes the exact packaged Bridge artifact');
  check(caps.bridge.loaded_board_inspection.profile === 'native-loaded-board-inspection-v1' && caps.bridge.loaded_board_inspection.request_available === true,
    'Actual local GUI conditionally advertises the native inspection profile');
  evidence.bridge_sha256 = caps.bridge.bridge_artifact_sha256;
  evidence.bridge_instance_id = caps.bridge.bridge_instance_id;
  const invalidRead = await raw('openpnp_get_board_inspection', { task_id: 'invalid-uuid' });
  check(invalidRead.isError === true && invalidRead.structuredContent.error.code === 'INVALID_ARGUMENT', 'Typed read rejects an invalid task identity');
  const invalidRequest = await raw('openpnp_request_board_inspection', { ...args.request, observations: [] });
  check(invalidRequest.isError === true && invalidRequest.structuredContent.error.code === 'INVALID_ARGUMENT', 'Typed request refuses observation fields');
  let operation = await call('openpnp_request_board_inspection', args.request);
  const operationId = operation.operation_id;
  check(typeof operationId === 'string', 'MCP request returns a native operation identity');
  const until = Date.now() + 12000;
  while (['accepted', 'running'].includes(operation.state)) {
    assert.ok(Date.now() < until, 'Native request did not complete within the owned helper bound; do not repeat it');
    await delay(40); operation = await call('openpnp_get_operation', { operation_id: operationId });
  }
  check(operation.state === 'succeeded' && operation.operation_id === operationId && operation.native_completion?.native_wrapper_succeeded === true,
    'MCP request reaches known successful native wrapper completion');
  const taskId = operation.result.task_id;
  const task = await call('openpnp_get_board_inspection', { task_id: taskId });
  check(task.task_id === taskId && task.task_active === true && task.pending.snapshot.loaded_board_id === args.request.loaded_board_id,
    'MCP read returns the same live task and exact selected native board');
  check(task.pending.snapshot.required_count === 2 && task.pending.snapshot.required_placements.length === 2,
    'MCP read retains both exact required placement rows');
  evidence.operation = operation; evidence.task = task;
  evidence.passed = true;
  evidence.stderr_tail = stderr;
} catch (error) {
  failure = error; evidence.error = { name: error.name, message: error.message };
} finally {
  // Only signal this SDK child's observed PID. Ending the MCP connection never releases the GUI lease.
  try {
    if (serverPid && alive(serverPid)) { process.kill(serverPid, 'SIGTERM'); evidence.server_sigterm_requested = true; }
    const until = Date.now() + 5000;
    while (serverPid && alive(serverPid) && Date.now() < until) await delay(25);
    evidence.server_gone_before_sdk_close = !serverPid || !alive(serverPid);
    await client?.close();
    evidence.server_pid = serverPid; evidence.server_pid_gone = !serverPid || !alive(serverPid);
    check(evidence.server_gone_before_sdk_close && evidence.server_pid_gone, 'Owned packaged MCP process exits and is reaped before SDK close');
  } catch (error) { evidence.cleanup_error = error.message; failure ??= error; }
  evidence.passed = evidence.passed && !failure;
  await writeFile(args.output_file, JSON.stringify(evidence, (key, value) => key === 'session_id' ? '<owned-lease>' : value, 2) + '\n', { flag: 'wx', mode: 0o600 });
}
if (failure) { console.error(failure); process.exitCode = 1; }
else console.log(JSON.stringify({ passed: true, task_id: evidence.task.task_id, operation_id: evidence.operation.operation_id, catalog_count: evidence.catalog_count }));
