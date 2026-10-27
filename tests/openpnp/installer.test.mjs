import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, mkdir, rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { parseArguments, endpoint, installBridge, atomicJson, doctor, main } from '../../plugins/openpnp/scripts/openpnp.mjs';

test('CLI rejects unknown, duplicated, and ambiguous options', () => {
  assert.deepEqual(parseArguments(['doctor', '--state-dir', '/tmp/state']), { command: 'doctor', options: { '--state-dir': '/tmp/state' } });
  for (const args of [['doctor', '--force', 'true'], ['doctor', '--state-dir'], ['doctor', '--state-dir', 'a', '--state-dir', 'b'], ['rm', '--state-dir', 'a']]) assert.throws(() => parseArguments(args));
  assert.throws(() => endpoint('http://127.0.0.1:12/path'));
});

test('bridge installation checks packaged hash, preserves journal, and refuses silent same-version replacement', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-install-')); t.after(() => rm(root, { recursive: true, force: true }));
  const source = path.join(root, 'source'); const state = path.join(root, 'state');
  await mkdir(path.join(source, 'bridge'), { recursive: true }); await mkdir(path.join(state, 'journal'), { recursive: true });
  await writeFile(path.join(state, 'journal', 'events.jsonl'), 'preserved-history');
  // Installer fixture is deliberately just bytes: native Java compatibility is tested by the separate native suite.
  const content = Buffer.from('installer test bytes');
  await writeFile(path.join(source, 'bridge', 'openpnp-codex-bridge.jar'), content);
  const manifest = { upstream_commit: '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c', bridge_version: '0.1.0', bridge_sha256: createHash('sha256').update(content).digest('hex') };
  await atomicJson(path.join(source, 'bridge', 'build-manifest.json'), manifest);
  const installed = await installBridge(state, source); assert.equal(installed.installed, true);
  assert.equal((await readFile(installed.bridge_jar)).toString(), content.toString());
  assert.equal(await readFile(path.join(state, 'journal', 'events.jsonl'), 'utf8'), 'preserved-history');
  assert.equal((await installBridge(state, source)).installed, true);
  await writeFile(installed.bridge_jar, 'modified');
  await assert.rejects(installBridge(state, source), /different content/);
  await writeFile(path.join(source, 'bridge', 'openpnp-codex-bridge.jar'), 'tampered');
  await assert.rejects(installBridge(state, source), /integrity/);
});

test('doctor reports absent native installation and connection instead of claiming a pass', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-doctor-')); t.after(() => rm(root, { recursive: true, force: true }));
  const result = await doctor(root); assert.equal(result.ok, false); assert.equal(result.hardware_qualification, 'unavailable');
  assert.ok(result.checks.some(check => check.check === 'bridge' && !check.ok));
});

test('uninstall refuses a live endpoint even when it rejects authentication', async t => {
  const state = await mkdtemp(path.join(os.tmpdir(), 'openpnp-uninstall-')); t.after(() => rm(state, { recursive: true, force: true }));
  const server = http.createServer((_request, response) => { response.writeHead(403); response.end('Forbidden'); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const installation = path.join(state, 'bridge', '0.1.0'); await mkdir(installation, { recursive: true });
  await writeFile(path.join(installation, 'openpnp-codex-bridge.jar'), 'must remain');
  await atomicJson(path.join(state, 'connection.json'), { url: `http://127.0.0.1:${server.address().port}/`, tokenFile: path.join(state, 'missing-token') });
  await assert.rejects(main(['uninstall-bridge', '--version', '0.1.0', '--state-dir', state]), /does not prove it stopped/);
  assert.equal(await readFile(path.join(installation, 'openpnp-codex-bridge.jar'), 'utf8'), 'must remain');
});
