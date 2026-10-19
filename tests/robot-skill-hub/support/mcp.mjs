import assert from 'node:assert/strict';
import { endpoint } from './package.mjs';

export const protocol = '2025-11-25';
export const names = ['find_robot_skills', 'get_hub_capabilities', 'inspect_robot_skill'];
export const annotations = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true };

// Test-only bounded protocol probe, not a bundled client or Codex substitute.
export class Probe {
  constructor(url, { fixture = false } = {}) {
    assert.ok(fixture ? /^http:\/\/127\.0\.0\.1:[1-9][0-9]*\/$/.test(url) : url === endpoint);
    this.url = url; this.trace = [];
  }
  async rpc(method, params, id) {
    assert.ok(this.trace.length < 6, 'Six-request bound');
    assert.ok(['initialize', 'notifications/initialized', 'tools/list', 'tools/call'].includes(method));
    if (method === 'tools/call') assert.ok(names.includes(params.name), 'No write or undeclared tool');
    const body = { jsonrpc: '2.0', method, params, ...(id === undefined ? {} : { id }) };
    const headers = { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream', 'MCP-Protocol-Version': protocol, DNT: '1', 'X-RSH-Telemetry': 'off', 'User-Agent': 'community-robot-skill-hub-contract-test/0.1.0' };
    const entry = { url: this.url, method: 'POST', headers, body }; this.trace.push(entry);
    const response = await fetch(this.url, { method: 'POST', headers, body: JSON.stringify(body), credentials: 'omit', redirect: 'error', signal: AbortSignal.timeout(5000) });
    entry.status = response.status;
    if (response.status !== (id === undefined ? 202 : 200)) { await response.body?.cancel(); assert.fail('Unexpected HTTP status: ' + response.status); }
    if (id !== undefined) assert.equal(response.headers.get('MCP-Protocol-Version'), protocol);
    const reader = response.body?.getReader(); const decoder = new TextDecoder(); let raw = '', bytes = 0;
    if (reader) {
      try {
        for (;;) {
          const { value, done } = await reader.read(); if (done) break;
          bytes += value.byteLength; assert.ok(bytes <= 131072, 'Bounded protocol response');
          raw += decoder.decode(value, { stream: true });
        }
      } finally { await reader.cancel(); }
    }
    raw += decoder.decode(); entry.response = raw;
    if (id === undefined) { assert.equal(raw, ''); return null; }
    const reply = JSON.parse(raw);
    assert.equal(reply.jsonrpc, '2.0'); assert.equal(reply.id, id); assert.equal(Object.hasOwn(reply, 'error'), false);
    const result = reply.result;
    if (method !== 'tools/call') return result;
    assert.equal(result.isError, false); assert.equal(result.content.length, 1); assert.equal(result.content[0].type, 'text');
    assert.deepEqual(JSON.parse(result.content[0].text), result.structuredContent);
    if (params.name !== 'get_hub_capabilities') assert.equal(result.structuredContent.execution_performed, false);
    return result.structuredContent;
  }
}

export async function qualify(probe) {
  const initialized = await probe.rpc('initialize', { protocolVersion: protocol, capabilities: {}, clientInfo: { name: 'community-robot-skill-hub-contract-test', version: '0.1.0' } }, 1);
  assert.equal(initialized.protocolVersion, protocol); assert.equal(initialized.serverInfo.name, 'robot-skill-hub');
  await probe.rpc('notifications/initialized', {});
  const tools = (await probe.rpc('tools/list', {}, 2)).tools;
  assert.deepEqual(tools.map(tool => tool.name).sort(), names);
  for (const tool of tools) assert.deepEqual(tool.annotations, annotations);
  const found = await probe.rpc('tools/call', { name: 'find_robot_skills', arguments: { q: 'joint state broadcaster', runtime: 'ros2-controllers', limit: 1 } }, 3);
  assert.ok(found.results.length > 0); const selected = found.results[0];
  assert.equal(selected.id, 'ros2-joint-state-broadcaster'); assert.ok(selected.record_version);
  const inspected = await probe.rpc('tools/call', { name: 'inspect_robot_skill', arguments: { skill_id: selected.id, record_version: selected.record_version } }, 4);
  assert.equal(inspected.record.id, selected.id); assert.equal(inspected.record.access.hub_prepare, false);
  const caps = await probe.rpc('tools/call', { name: 'get_hub_capabilities', arguments: {} }, 5);
  assert.equal(caps.onboarding.mcp_read_only, true); assert.equal(caps.onboarding.source_or_robot_execution, false);
  assert.equal(caps.public_discovery.authentication_required, false);
  assert.equal(probe.trace.length, 6);
  return { server: initialized.serverInfo, selected: { id: selected.id, record_version: selected.record_version }, trace: probe.trace };
}
