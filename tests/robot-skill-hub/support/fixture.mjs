import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { annotations, names, protocol } from './mcp.mjs';

export async function fixture(t, handler) {
  const requests = [];
  const server = createServer(async (request, response) => {
    try {
      let raw = ''; for await (const piece of request) { raw += piece; assert.ok(raw.length <= 16384); }
      const body = JSON.parse(raw); requests.push({ headers: request.headers, body });
      if (handler) { handler(request, response, body); return; }
      const selected = { id: 'ros2-joint-state-broadcaster', record_version: 'source-' + 'a'.repeat(64) };
      let result;
      if (body.method === 'notifications/initialized') { response.writeHead(202).end(); return; }
      if (body.method === 'initialize') result = { protocolVersion: protocol, serverInfo: { name: 'robot-skill-hub', version: 'fixture-not-a-live-result' } };
      else if (body.method === 'tools/list') result = { tools: names.map(name => ({ name, annotations })) };
      else {
        let data;
        if (body.params.name === 'find_robot_skills') data = { execution_performed: false, results: [selected] };
        else if (body.params.name === 'inspect_robot_skill') {
          assert.deepEqual(body.params.arguments, { skill_id: selected.id, record_version: selected.record_version });
          data = { execution_performed: false, record: { id: selected.id, access: { hub_prepare: false } } };
        } else data = { onboarding: { mcp_read_only: true, source_or_robot_execution: false }, public_discovery: { authentication_required: false } };
        result = { isError: false, structuredContent: data, content: [{ type: 'text', text: JSON.stringify(data) }] };
      }
      response.writeHead(200, { 'Content-Type': 'application/json', 'MCP-Protocol-Version': protocol }).end(JSON.stringify({ jsonrpc: '2.0', id: body.id, result }));
    } catch (error) { response.writeHead(500).end(JSON.stringify({ error: error.message })); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  return { url: `http://127.0.0.1:${server.address().port}/`, requests };
}
