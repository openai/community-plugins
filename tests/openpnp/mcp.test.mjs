import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, cp, symlink } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
test('official MCP client initializes bundled sparse-install server, discovers resources, imports and validates offline', async t => {
  const state = await mkdtemp(path.join(os.tmpdir(), 'openpnp-mcp-')); t.after(() => rm(state, { recursive: true, force: true }));
  const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(root, 'plugins/openpnp/mcp/server.mjs'), '--stdio'],
    cwd: state, env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: state }, stderr: 'pipe' });
  const client = new Client({ name: 'openpnp-test', version: '1.0.0' });
  let stderr = ''; transport.stderr?.on('data', data => { stderr += data.toString(); });
  t.after(() => client.close());
  await client.connect(transport);
  const { tools } = await client.listTools(); assert.ok(tools.length >= 25);
  assert.ok(tools.find(tool => tool.name === 'openpnp_plan_motion').inputSchema.required.includes('session_id'));
  const capabilities = await client.callTool({ name: 'openpnp_get_capabilities', arguments: {} });
  assert.equal(capabilities.structuredContent.connected, false); assert.equal(capabilities.structuredContent.machine_state, 'unknown');
  const resources = await client.listResources(); assert.equal(resources.resources[0].uri, 'openpnp://capabilities');
  const resource = await client.readResource({ uri: 'openpnp://capabilities' }); assert.equal(JSON.parse(resource.contents[0].text).connected, false);
  const rejected = await client.callTool({ name: 'openpnp_get_status', arguments: { arbitrary: 'value' } });
  assert.equal(rejected.isError, true); assert.equal(rejected.structuredContent.error.code, 'INVALID_ARGUMENT');
  const imported = await client.callTool({ name: 'openpnp_import_job', arguments: {
    format: 'reference-csv', content: 'Ref,Val,Package,PosX,PosY,Rot,Side\nR1,10k,R_0603,10,20,0,top\n', units: 'mm', widthMm: 50, heightMm: 40,
  } });
  assert.notEqual(imported.isError, true, JSON.stringify(imported));
  assert.match(imported.structuredContent.artifact_id, /^[a-f0-9]{64}$/);
  const validation = await client.callTool({ name: 'openpnp_validate_imported_job', arguments: { artifact_id: imported.structuredContent.artifact_id } });
  assert.notEqual(validation.isError, true); assert.equal(validation.structuredContent.validation.productionQualified, false);
  const artifact = await client.callTool({ name: 'openpnp_get_artifact', arguments: { artifact_id: imported.structuredContent.artifact_id } });
  assert.equal(artifact.structuredContent.content.boards[0].placements[0].ref, 'R1');
  assert.ok(!stderr.includes('Bearer'));
});

test('actual packaged stdio server initializes through a directory path alias in a sparse copied installation', async t => {
  const state = await mkdtemp(path.join(os.tmpdir(), 'openpnp-mcp-alias-'));
  t.after(() => rm(state, { recursive: true, force: true }));
  const copied = path.join(state, 'copied-mcp');
  await cp(path.join(root, 'plugins/openpnp/mcp'), copied, { recursive: true });
  const alias = path.join(state, 'installation-alias');
  await symlink(copied, alias, process.platform === 'win32' ? 'junction' : 'dir');
  const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(alias, 'server.mjs'), '--stdio'],
    cwd: state, env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: path.join(state, 'private-state'),
      OPENPNP_CONNECTION_FILE: path.join(state, 'intentionally-absent.json') }, stderr: 'pipe' });
  const client = new Client({ name: 'openpnp-alias-regression', version: '1.0.0' });
  t.after(() => client.close());
  await client.connect(transport);
  const response = await client.callTool({ name: 'openpnp_get_capabilities', arguments: {} });
  assert.equal(response.structuredContent.connected, false);
  assert.equal(response.structuredContent.machine_state, 'unknown');
  assert.ok((await client.listTools()).tools.some(tool => tool.name === 'openpnp_read_response_page'));
});
