import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, cp, rm, mkdir, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createInterface } from 'node:readline';
import { once } from 'node:events';
import test from 'node:test';

test('packaged Explorer starts without node_modules and serves its tools', { timeout: 15000 }, async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'lighthouse-package-'));
  let child;
  try {
    await mkdir(path.join(root, 'explorer'), { recursive: true });
    await cp('plugins/lighthouse/explorer/dist', path.join(root, 'explorer/dist'), { recursive: true });
    const config = JSON.parse(await readFile('plugins/lighthouse/.mcp.json', 'utf8'));
    const server = config.mcpServers.lighthouse;
    child = spawn(server.command, server.args, { cwd: root, stdio: ['pipe', 'pipe', 'pipe'] });
    let errors = '';
    child.stderr.setEncoding('utf8').on('data', data => { errors += data; });
    const lines = createInterface({ input: child.stdout });
    const replies = lines[Symbol.asyncIterator]();
    const send = value => child.stdin.write(JSON.stringify(value) + '\n');
    send({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {
      protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'package-test', version: '1.0.0' }
    } });
    const init = await replies.next();
    assert.equal(init.done, false, errors);
    const hello = JSON.parse(init.value);
    assert.equal(hello.id, 1);
    assert.ok(hello.result.serverInfo);
    send({ jsonrpc: '2.0', method: 'notifications/initialized' });
    send({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} });
    let response;
    do {
      const line = await replies.next();
      assert.equal(line.done, false, errors);
      response = JSON.parse(line.value);
    } while (response.id !== 2);
    const names = response.result.tools.map(tool => tool.name);
    assert.ok(names.includes('lighthouse_explorer_home'), JSON.stringify(names));
    assert.ok(names.includes('lighthouse_open_benchmark'), JSON.stringify(names));
    child.stdin.end();
    await once(child, 'exit');
    assert.equal(child.exitCode, 0, errors);
  } finally {
    if (child && child.exitCode === null) child.kill();
    await rm(root, { recursive: true, force: true });
  }
});
