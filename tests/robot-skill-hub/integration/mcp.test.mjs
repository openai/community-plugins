import assert from 'node:assert/strict';
import test from 'node:test';
import { fixture } from '../support/fixture.mjs';
import { Probe, qualify } from '../support/mcp.mjs';
import { inspectPackage } from '../support/package.mjs';

test('HTTP fixture exercises six MCP messages, selected-version handoff and operator opt-out (not a live result)', async t => {
  const server = await fixture(t); const result = await qualify(new Probe(server.url, { fixture: true }));
  assert.equal(result.trace.length, 6); assert.equal(server.requests.length, 6);
  for (const request of server.requests) {
    assert.equal(request.headers.dnt, '1'); assert.equal(request.headers['x-rsh-telemetry'], 'off');
    assert.equal(request.headers.authorization, undefined); assert.equal(request.headers.cookie, undefined);
  }
});

test('opt-in actual packaged public MCP endpoint contract; not an installed Codex or adoption test', { skip: process.env.RSH_PLUGIN_LIVE_MCP !== '1' ? 'Explicit RSH_PLUGIN_LIVE_MCP=1 required: six anonymous opted-out public MCP reads.' : false, timeout: 45000 }, async t => {
  const { endpoint } = inspectPackage(); const result = await qualify(new Probe(endpoint));
  t.diagnostic(JSON.stringify({ actual_public_MCP_protocol_check: result, installed_Codex_client: false, source_or_robot_execution: false, external_adoption: false }));
});
