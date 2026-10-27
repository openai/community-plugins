import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, chmod, symlink, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { BridgeClient, loadConnection, validateEndpoint } from '../../src/openpnp/node/client.mjs';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { ArtifactStore } from '../../src/openpnp/node/artifacts.mjs';
import { TOOL_DEFINITIONS } from '../../src/openpnp/node/contracts.mjs';

const token = 'T'.repeat(48);
async function fixture(t, handler) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-transport-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const server = http.createServer(handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const tokenFile = path.join(root, 'token'); const connectionFile = path.join(root, 'connection.json');
  await writeFile(tokenFile, token, { mode: 0o600 });
  await writeFile(connectionFile, JSON.stringify({ url: `http://127.0.0.1:${server.address().port}`, tokenFile }), { mode: 0o600 });
  return { root, tokenFile, connectionFile, client: new BridgeClient({ connectionFile, timeoutMs: 150 }) };
}

test('loopback connection rejects remote destinations, redirects, URL secrets, and paths', () => {
  for (const url of ['https://127.0.0.1:8000/', 'http://example.com:1234', 'http://localhost:1234', 'http://127.0.0.1:1234/rpc', 'http://u:p@127.0.0.1:1234/', 'http://127.0.0.1:1234/?token=x', 'http://127.0.0.1/']) assert.throws(() => validateEndpoint(url));
  assert.equal(validateEndpoint('http://127.0.0.1:8181/').hostname, '127.0.0.1');
  assert.equal(validateEndpoint('http://[::1]:8181/').hostname, '[::1]');
});

test('transport sends authenticated typed RPC and returns its result', async t => {
  const fixtureData = await fixture(t, async (request, response) => {
    assert.equal(request.url, '/rpc'); assert.equal(request.headers.authorization, `Bearer ${token}`);
    let body = ''; for await (const part of request) body += part;
    assert.deepEqual(JSON.parse(body), { method: 'openpnp_get_status', params: {} });
    response.setHeader('content-type', 'application/json'); response.end(JSON.stringify({ result: { homed: null } }));
  });
  assert.deepEqual(await fixtureData.client.call('openpnp_get_status'), { homed: null });
});

test('credential files reject public permissions and symbolic links', async t => {
  const f = await fixture(t, (_request, response) => response.end('{}'));
  if (process.platform !== 'win32') {
    await chmod(f.tokenFile, 0o644);
    await assert.rejects(loadConnection(f.connectionFile), { code: 'INSECURE_CREDENTIAL_FILE' });
    await chmod(f.tokenFile, 0o600);
  }
  const link = path.join(f.root, 'connection-link'); await symlink(f.connectionFile, link);
  await assert.rejects(loadConnection(link), { code: 'INVALID_CONNECTION' });
});

test('lost mutation response is outcome-unknown and never retried', async t => {
  let calls = 0;
  const f = await fixture(t, (_request, response) => { calls++; setTimeout(() => response.end('{}'), 500); });
  await assert.rejects(f.client.call('openpnp_home_machine', { request_id: 'lost-home' }, { mutating: true }), error => {
    assert.equal(error.code, 'OUTCOME_UNKNOWN'); assert.equal(error.details.request_id, 'lost-home'); return true;
  });
  assert.equal(calls, 1);
});

test('redirects and malformed successful responses never trigger another destination or physical retry', async t => {
  let calls = 0;
  const f = await fixture(t, (_request, response) => { calls++; response.writeHead(307, { location: 'http://127.0.0.1:1/' }); response.end(); });
  await assert.rejects(f.client.call('openpnp_home_machine', { request_id: 'redirect' }, { mutating: true }), { code: 'OUTCOME_UNKNOWN' });
  assert.equal(calls, 1);
});

test('known authentication rejection is distinct from unknown admission', async t => {
  const f = await fixture(t, (_request, response) => { response.writeHead(403); response.end(token); });
  await assert.rejects(f.client.call('openpnp_home_machine', { request_id: 'auth' }, { mutating: true }), error => {
    assert.equal(error.code, 'BRIDGE_AUTH_REJECTED'); assert.ok(!JSON.stringify(error).includes(token)); return true;
  });
});

test('null errors and malformed operation receipts cannot masquerade as successful mutations', async t => {
  let envelope;
  const f = await fixture(t, (_request, response) => response.end(JSON.stringify(envelope)));
  for (const value of [{ error: null }, { result: null }, { result: [] }, { result: {} }, { result: { operation_id: 'op', state: 'fiction' } }, { result: {}, error: {} }]) {
    envelope = value;
    await assert.rejects(f.client.call('openpnp_home_machine', { request_id: 'original' }, { mutating: true }), error => {
      assert.equal(error.code, 'OUTCOME_UNKNOWN'); assert.equal(error.details.request_id, 'original'); return true;
    });
  }
  envelope = { result: { operation_id: 'op', state: 'accepted' } };
  assert.equal((await f.client.call('openpnp_home_machine', { request_id: 'original' }, { mutating: true })).operation_id, 'op');
});

test('bridge errors redact tokens in nested details as well as messages', async t => {
  const f = await fixture(t, (_request, response) => response.end(JSON.stringify({ error: { code: 'REJECTED', message: token, details: { nested: [{ secret: token }] } } })));
  await assert.rejects(f.client.call('openpnp_get_status'), error => { assert.ok(!JSON.stringify(error).includes(token)); assert.equal(error.message, '[REDACTED]'); return true; });
});

test('strict tool schemas reject additional properties, nonfinite numbers, and raw control hooks before native calls', async () => {
  const seen = [];
  const runtime = new OpenPnpRuntime({ client: { call: async (...args) => { seen.push(args); return { tools: [] }; } } });
  for (const [name, args] of [
    ['openpnp_get_status', { eval: 'System.exit(0)' }],
    ['openpnp_plan_motion', { session_id: 's', units: 'mm', x: Infinity, y: 0, z: 0 }],
    ['openpnp_execute_motion', { session_id: 's', request_id: 'r', plan_id: 'p', gcode: 'G0 X1' }],
    ['openpnp_import_job', { format: 'reference-csv', content: 'x', __extra: true }],
  ]) await assert.rejects(runtime.call(name, args), { code: 'INVALID_ARGUMENT' });
  await assert.rejects(runtime.call('openpnp_raw_serial', {}), { code: 'UNKNOWN_TOOL' });
  assert.equal(seen.length, 0);
  assert.ok(TOOL_DEFINITIONS.every(tool => tool.inputSchema.additionalProperties === false));
});

test('missing native capability blocks forwarding while offline tools remain discoverable', async () => {
  const seen = [];
  const runtime = new OpenPnpRuntime({ client: { call: async (...args) => { seen.push(args[0]); return { tools: ['openpnp_get_status'], hardware_qualified: false, upstream_commit: PINNED_UPSTREAM, schema_version: 1, bridge_version: '0.1.0' }; } } });
  await assert.rejects(runtime.call('openpnp_home_machine', { request_id: 'r', session_id: 's' }), { code: 'UNSUPPORTED_CAPABILITY' });
  assert.deepEqual(seen, ['openpnp_get_capabilities']);
  const capabilities = await runtime.call('openpnp_get_capabilities');
  assert.ok(capabilities.tools.includes('openpnp_import_job')); assert.ok(!capabilities.tools.includes('openpnp_home_machine'));
});

test('an incompatible native build cannot advertise or execute machine tools', async () => {
  const runtime = new OpenPnpRuntime({ client: { call: async () => ({ tools: ['openpnp_home_machine'], schema_version: 999, upstream_commit: PINNED_UPSTREAM, bridge_version: '0.1.0' }) } });
  await assert.rejects(runtime.call('openpnp_home_machine', { request_id: 'r', session_id: 's' }), { code: 'INCOMPATIBLE_BRIDGE' });
  const capabilities = await runtime.call('openpnp_get_capabilities'); assert.equal(capabilities.connected, false); assert.equal(capabilities.connection_error.code, 'INCOMPATIBLE_BRIDGE');
});

test('artifact store checks immutable content hashes and rejects path traversal', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-artifact-')); t.after(() => rm(root, { recursive: true, force: true }));
  const store = new ArtifactStore(root); const job = { id: 'job', placements: [{ x: 1 }] };
  const artifact = await store.put(job); assert.deepEqual(await store.get(artifact.artifact_id), job);
  assert.equal((await store.put(job)).artifact_id, artifact.artifact_id);
  await assert.rejects(store.get('../../secret'), { code: 'INVALID_ARTIFACT_ID' });
  await writeFile(path.join(root, 'artifacts', `${artifact.artifact_id}.json`), '{}');
  await assert.rejects(store.get(artifact.artifact_id), { code: 'ARTIFACT_INTEGRITY_FAILURE' });
});
