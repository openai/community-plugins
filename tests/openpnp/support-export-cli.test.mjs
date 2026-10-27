// SPDX-License-Identifier: Apache-2.0
// Real Node CLI children, synthetic installed files only. No Java, bridge or network calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, writeFile, realpath, readdir, rm, lstat, symlink } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { parseArguments, supportSelections } from '../../plugins/openpnp/scripts/openpnp.mjs';

const cli = fileURLToPath(new URL('../../plugins/openpnp/scripts/openpnp.mjs', import.meta.url));
const id = '11111111-1111-4111-8111-111111111111';
const imageId = '22222222-2222-4222-8222-222222222222';
const documentId = '33333333-3333-4333-8333-333333333333';
const sha = data => createHash('sha256').update(data).digest('hex');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jg5cAAAAASUVORK5CYII=', 'base64');
async function fixture(t) {
  const root = await realpath(await mkdtemp(path.join(os.tmpdir(), 'openpnp-support-cli-')));
  t.after(() => rm(root, { recursive: true, force: true }));
  const state = path.join(root, 'state'), output = path.join(root, 'support.tar');
  const jar = path.join(state, 'bridge/0.1.0/openpnp-codex-bridge.jar');
  await mkdir(path.dirname(jar), { recursive: true }); await mkdir(path.join(state, 'journal'));
  const jarBytes = Buffer.from('synthetic installer fixture, no Java implementation'); await writeFile(jar, jarBytes);
  const manifest = { upstream_commit: '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c', bridge_version: '0.1.0', bridge_sha256: sha(jarBytes) };
  await writeFile(path.join(path.dirname(jar), 'build-manifest.json'), JSON.stringify(manifest));
  await writeFile(path.join(state, 'installation.json'), JSON.stringify({ schema_version: 1, version: '0.1.0', bridge_jar: jar,
    bridge_sha256: manifest.bridge_sha256, upstream_commit: manifest.upstream_commit }));
  await writeFile(path.join(state, 'journal/machine-id'), id);
  const journal = JSON.stringify({ sequence: 1, type: 'operation', payload: { operation_id: id, method: 'openpnp_start_job', state: 'outcome_unknown', result: { repeat_action_performed: false } } }) + '\n';
  await writeFile(path.join(state, 'journal/operations.jsonl'), journal);
  await writeFile(path.join(state, 'bridge.token'), 'private-test-credential-must-not-be-exported');
  await writeFile(path.join(state, 'connection.json'), JSON.stringify({ url: 'http://127.0.0.1:1/', tokenFile: '/not-to-be-opened' }));
  return { root, state, output, journal };
}
async function inventory(root) {
  const out = {};
  async function visit(directory, prefix = '') {
    for (const name of (await readdir(directory)).sort()) {
      const file = path.join(directory, name), key = prefix + name, info = await lstat(file);
      if (info.isDirectory()) await visit(file, key + '/');
      else out[key] = { sha256: sha(await readFile(file)), mode: info.mode, size: info.size };
    }
  }
  await visit(root); return out;
}
async function execute(t, args, { env = {}, preload, entry = cli } = {}) {
  const childEnv = { ...process.env, ...env }; delete childEnv.NODE_OPTIONS;
  const child = spawn(process.execPath, [...(preload ? ['--import', preload] : []), entry, ...args],
    { env: childEnv, stdio: ['ignore', 'pipe', 'pipe'], shell: false });
  let stdout = '', stderr = '', timedOut = false;
  const timer = setTimeout(() => { timedOut = true; child.kill('SIGKILL'); }, 10000);
  child.stdout.on('data', chunk => { stdout += chunk; if (stdout.length > 65536) child.kill('SIGKILL'); });
  child.stderr.on('data', chunk => { stderr += chunk; if (stderr.length > 65536) child.kill('SIGKILL'); });
  const completion = new Promise((resolve, reject) => {
    child.once('error', reject); child.once('close', (code, signal) => resolve({ code, signal, stdout, stderr, timedOut }));
  }).finally(() => clearTimeout(timer));
  t.after(async () => { if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL'); await completion; });
  const result = await completion; assert.equal(result.timedOut, false); assert.equal(result.signal, null); return result;
}
function readTar(data) {
  const entries = new Map(); let offset = 0;
  while (!data.subarray(offset, offset + 512).every(byte => byte === 0)) {
    const header = data.subarray(offset, offset + 512);
    const name = header.subarray(0, 100).toString().split('\0')[0]; const length = parseInt(header.toString('ascii', 124, 135), 8);
    offset += 512; entries.set(name, data.subarray(offset, offset + length)); offset += Math.ceil(length / 512) * 512;
  }
  return entries;
}
async function artifact(f, artifactId, data, mime, metadata) {
  await writeFile(path.join(f.state, `journal/${artifactId}.artifact`), data);
  await writeFile(path.join(f.state, `journal/${artifactId}.metadata.json`), JSON.stringify({ artifact_id: artifactId, sha256: sha(data), size: data.length, mime_type: mime, metadata }));
}

test('CLI parser preserves explicit selection order and separates typed artifact scopes', () => {
  const operations = `${id},${imageId}`, artifacts = `${imageId}:${sha(png)}:camera,${documentId}:${'a'.repeat(64)}:job-document`;
  const parsed = parseArguments(['support-export', '--state-dir', '/state', '--output', '/out.tar', '--operation-ids', operations, '--artifacts', artifacts]);
  assert.equal(parsed.command, 'support-export'); assert.deepEqual(supportSelections(parsed.options), {
    operationIds: [id, imageId], artifactSelections: [
      { artifact_id: imageId, sha256: sha(png), kind: 'camera' }, { artifact_id: documentId, sha256: 'a'.repeat(64), kind: 'job-document' },
    ],
  });
  assert.deepEqual(supportSelections({}), { operationIds: [], artifactSelections: [] });
  for (const input of [{ '--operation-ids': '' }, { '--operation-ids': 1 }, { '--operation-ids': 'x'.repeat(4097) },
    { '--artifacts': '' }, { '--artifacts': 'x'.repeat(4097) }, { '--artifacts': `${imageId}:camera` }, { '--artifacts': `${imageId}:${'a'.repeat(64)}:camera:extra` }])
    assert.throws(() => supportSelections(input));
});

test('actual CLI with no selections exports summaries only and does not touch installed state', async t => {
  const f = await fixture(t), before = await inventory(f.state);
  const result = await execute(t, ['support-export', '--state-dir', f.state, '--output', f.output]);
  assert.equal(result.code, 0, result.stderr); assert.equal(result.stderr, '');
  const receipt = JSON.parse(result.stdout), data = await readFile(f.output), entries = readTar(data);
  assert.equal(receipt.sha256, sha(data)); assert.equal(receipt.size, data.length);
  assert.equal(receipt.selected_operation_count, 0); assert.equal(receipt.selected_artifact_count, 0); assert.equal(receipt.selected_records, 0);
  assert.equal(entries.get('operations.jsonl').length, 0);
  assert.equal(JSON.parse(entries.get('selection.json')).unresolved_operation_inventory[0].state, 'outcome_unknown');
  assert.ok(!data.includes(Buffer.from('private-test-credential'))); assert.deepEqual(await inventory(f.state), before);
});

test('actual CLI accepts explicit operation/image/document selections and preserves exact selected bytes', async t => {
  const f = await fixture(t), zip = Buffer.from([80,75,3,4,1,2,3,4]); // Opaque signature fixture only.
  await artifact(f, imageId, png, 'image/png', { camera_id: 'C1' });
  await artifact(f, documentId, zip, 'application/zip', { kind: 'native-job-document' });
  const before = await inventory(f.state);
  const result = await execute(t, ['support-export', '--state-dir', f.state, '--output', f.output, '--operation-ids', id,
    '--artifacts', `${imageId}:${sha(png)}:camera,${documentId}:${sha(zip)}:job-document`]);
  assert.equal(result.code, 0, result.stderr); const receipt = JSON.parse(result.stdout), entries = readTar(await readFile(f.output));
  assert.equal(receipt.selected_records, 1); assert.equal(receipt.selected_artifact_count, 2);
  assert.deepEqual(entries.get(`artifacts/${imageId}.png`), png); assert.deepEqual(entries.get(`artifacts/${documentId}.zip`), zip);
  assert.equal(JSON.parse(entries.get('operations.jsonl')).facts.state, 'outcome_unknown'); assert.deepEqual(await inventory(f.state), before);
});

test('actual CLI uses explicit environment state and works through a symlinked entrypoint', async t => {
  const f = await fixture(t), alias = path.join(f.root, 'cli.mjs'); await symlink(cli, alias);
  const result = await execute(t, ['support-export', '--output', f.output, '--operation-ids', id], { entry: alias, env: { OPENPNP_STATE_DIR: f.state } });
  assert.equal(result.code, 0, result.stderr); assert.equal(JSON.parse(result.stdout).selected_records, 1);
});

test('actual CLI rejects malformed options/selections before reading nonexistent state', async t => {
  const f = await fixture(t), before = await inventory(f.state); const missing = path.join(f.root, 'does-not-exist');
  const cases = [
    ['--operation-ids', `${id},`], ['--operation-ids', `${id},${id}`], ['--operation-ids', 'not-a-uuid'],
    ['--artifacts', `${imageId}:${sha(png)}:configuration`], ['--artifacts', `${imageId}:bad:camera`],
    ['--artifacts', `${imageId}:${sha(png)}:camera,${imageId}:${sha(png)}:camera`],
  ];
  for (const tail of cases) {
    const result = await execute(t, ['support-export', '--state-dir', missing, '--output', f.output, ...tail]);
    assert.equal(result.code, 1); assert.equal(result.stdout, ''); assert.equal(JSON.parse(result.stderr).code, 'SUPPORT_SELECTION');
  }
  for (const tail of [['--artifacts', `${imageId}:camera`], ['--output', f.output], ['--raw-journal', 'true'], ['--operation-ids']]) {
    const result = await execute(t, ['support-export', '--state-dir', missing, '--output', f.output, ...tail]);
    assert.equal(result.code, 1); assert.equal(result.stdout, ''); assert.equal(typeof JSON.parse(result.stderr).error, 'string');
  }
  await assert.rejects(lstat(missing), { code: 'ENOENT' }); await assert.rejects(lstat(f.output), { code: 'ENOENT' });
  assert.deepEqual(await inventory(f.state), before);
});

test('actual CLI preserves support error codes and never overwrites prior evidence', async t => {
  const f = await fixture(t), before = await inventory(f.state);
  let result = await execute(t, ['support-export', '--state-dir', f.state]);
  assert.equal(result.code, 1); assert.equal(JSON.parse(result.stderr).code, 'SUPPORT_PATH');
  result = await execute(t, ['support-export', '--state-dir', f.state, '--output', f.output, '--operation-ids', documentId]);
  assert.equal(result.code, 1); assert.equal(JSON.parse(result.stderr).code, 'SUPPORT_OPERATION_NOT_FOUND');
  await writeFile(f.output, 'pre-existing evidence');
  result = await execute(t, ['support-export', '--state-dir', f.state, '--output', f.output]);
  assert.equal(result.code, 1); assert.equal(JSON.parse(result.stderr).code, 'SUPPORT_OUTPUT_EXISTS');
  assert.equal(await readFile(f.output, 'utf8'), 'pre-existing evidence'); assert.deepEqual(await inventory(f.state), before);
});

test('actual CLI keeps a published archive and the uncertainty code when final directory sync fails', async t => {
  const f = await fixture(t), before = await inventory(f.state); const preload = path.join(f.root, 'controlled-sync-failure.mjs');
  // Test-only fault injection at one specific directory FileHandle.sync. All reads,
  // archive bytes, exclusive publication and CLI error handling remain real.
  await writeFile(preload, `import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
const original = fs.promises.open;
fs.promises.open = async function(file, flags, ...rest) {
  const handle = await original.call(this, file, flags, ...rest);
  if (file === process.env.SUPPORT_TEST_OUTPUT_PARENT && flags === 'r') {
    handle.sync = async () => {
      if (!fs.existsSync(process.env.SUPPORT_TEST_OUTPUT)) throw new Error('Fault was injected before publication');
      fs.writeFileSync(process.env.SUPPORT_TEST_FAULT_MARKER, 'injected-after-output-publication');
      throw Object.assign(new Error('Controlled final directory sync failure'), { code: 'EIO' });
    };
  }
  return handle;
};
syncBuiltinESMExports();
`);
  const marker = path.join(f.root, 'fault.marker');
  const result = await execute(t, ['support-export', '--state-dir', f.state, '--output', f.output, '--operation-ids', id], {
    preload, env: { SUPPORT_TEST_OUTPUT_PARENT: f.root, SUPPORT_TEST_OUTPUT: f.output, SUPPORT_TEST_FAULT_MARKER: marker },
  });
  assert.equal(result.code, 1, result.stderr); assert.equal(result.stdout, '');
  assert.equal(JSON.parse(result.stderr).code, 'SUPPORT_PUBLICATION_UNCERTAIN');
  assert.equal(await readFile(marker, 'utf8'), 'injected-after-output-publication');
  const data = await readFile(f.output); assert.equal(JSON.parse(readTar(data).get('operations.jsonl')).facts.state, 'outcome_unknown');
  const retry = await execute(t, ['support-export', '--state-dir', f.state, '--output', f.output]);
  assert.equal(retry.code, 1); assert.equal(JSON.parse(retry.stderr).code, 'SUPPORT_OUTPUT_EXISTS');
  assert.deepEqual(await readFile(f.output), data); assert.deepEqual(await inventory(f.state), before);
  assert.deepEqual((await readdir(f.root)).filter(name => name.endsWith('.tmp')), []);
});
