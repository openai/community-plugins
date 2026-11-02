import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { copiedPackage, inspectPackage, json } from '../support/package.mjs';
import { fixture } from '../support/fixture.mjs';
import { Probe } from '../support/mcp.mjs';

for (const [label, mutate] of [
  ['credential-bearing URL', value => { value.mcpServers['robot-skill-hub'].url = 'https://fixture-user:fixture-not-a-secret@api.superfastrobotics.com/mcp'; }],
  ['different remote endpoint', value => { value.mcpServers['robot-skill-hub'].url = 'https://example.invalid/mcp'; }],
  ['credential header', value => { value.mcpServers['robot-skill-hub'].headers = { Authorization: 'fixture-not-a-secret' }; }],
  ['local command', value => { value.mcpServers['robot-skill-hub'].command = 'node'; }],
]) test(`distribution audit rejects ${label} before any connection`, t => {
  const root = copiedPackage(t); const filename = path.join(root, 'mcp.json'); const config = json(filename); mutate(config); writeFileSync(filename, JSON.stringify(config));
  assert.throws(() => inspectPackage(root));
});

test('distribution audit rejects an escaping native MCP path', t => {
  const root = copiedPackage(t); const filename = path.join(root, '.codex-plugin/plugin.json'); const manifest = json(filename); manifest.mcpServers = '../outside.json'; writeFileSync(filename, JSON.stringify(manifest));
  assert.throws(() => inspectPackage(root));
});

test('HTTP failure stops after one request, with no automatic retry', async t => {
  const server = await fixture(t, (_request, response) => response.writeHead(503).end()); const probe = new Probe(server.url, { fixture: true });
  await assert.rejects(() => probe.rpc('tools/list', {}, 1)); assert.equal(probe.trace.length, 1); assert.equal(server.requests.length, 1);
});

test('HTTP redirect is refused and not followed', async t => {
  const target = await fixture(t); const source = await fixture(t, (_request, response) => response.writeHead(302, { Location: target.url }).end());
  const probe = new Probe(source.url, { fixture: true }); await assert.rejects(() => probe.rpc('tools/list', {}, 1));
  assert.equal(source.requests.length, 1); assert.equal(target.requests.length, 0);
});

test('protocol probe rejects undeclared tools without sending a request', async t => {
  const server = await fixture(t); const probe = new Probe(server.url, { fixture: true });
  await assert.rejects(() => probe.rpc('tools/call', { name: 'submit_private_work', arguments: {} }, 1));
  assert.equal(probe.trace.length, 0); assert.equal(server.requests.length, 0);
});
