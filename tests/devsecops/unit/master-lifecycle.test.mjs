import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { requireUvVersion } from '../support/uv.mjs';

const suite = fileURLToPath(new URL('../', import.meta.url));
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
async function until(predicate, milliseconds = 8000) {
  const deadline = Date.now() + milliseconds;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await pause(25);
  }
  assert.fail('Synthetic runner condition did not occur before the bounded test deadline');
}
function alive(pid) {
  try { process.kill(pid, 0); return true; } catch (error) { if (error.code === 'ESRCH') return false; throw error; }
}
function stopGroup(pid) {
  try { process.kill(-pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
}
function fixture(t, validator) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'devsecops-runner-contract-')));
  const privateRoot = path.join(os.homedir(), '.cache/devsecops');
  fs.mkdirSync(privateRoot, { recursive: true, mode: 0o700 });
  const cache = fs.mkdtempSync(path.join(privateRoot, 'community-runner-test-'));
  for (const directory of ['scripts', 'plugins/devsecops', 'tests/devsecops/support'])
    fs.mkdirSync(path.join(root, directory), { recursive: true });
  for (const relative of ['run.mjs', 'support/package.mjs', 'support/uv.mjs'])
    fs.copyFileSync(path.join(suite, relative), path.join(root, 'tests/devsecops', relative));
  fs.writeFileSync(path.join(root, 'plugins/devsecops/README.md'), 'Synthetic runner fixture; never a plugin qualification.\n');
  fs.writeFileSync(path.join(root, 'scripts/validate-marketplace.mjs'), validator);
  fs.writeFileSync(path.join(root, 'tests/devsecops/contracts.mjs'), '// Synthetic test prerequisite; no nested master.\n');
  const pids = path.join(root, 'pids.json');
  let master;
  t.after(async () => {
    if (fs.existsSync(pids)) stopGroup(JSON.parse(fs.readFileSync(pids)).leader);
    if (master && alive(master.pid)) stopGroup(master.pid);
    fs.rmSync(cache, { recursive: true, force: true });
    fs.rmSync(root, { recursive: true, force: true });
  });
  function launch(extra = {}) {
    master = spawn(process.execPath, [path.join(root, 'tests/devsecops/run.mjs')], {
      cwd: root, detached: true, stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, DEVSECOPS_TEST_CACHE: cache, DEVSECOPS_TEST_NETWORK_SANDBOX: '0', TEST_DESCENDANT_FILE: pids, ...extra },
    });
    let output = '';
    for (const stream of [master.stdout, master.stderr]) stream.on('data', data => { output = (output + data).slice(-32000); });
    const finished = new Promise((resolve, reject) => { master.on('error', reject); master.on('close', code => resolve(code)); });
    return { master, finished, output: () => output };
  }
  function receipt() {
    const runs = fs.readdirSync(path.join(cache, 'results'));
    assert.equal(runs.length, 1);
    return JSON.parse(fs.readFileSync(path.join(cache, 'results', runs[0], 'master.json')));
  }
  return { root, pids, launch, receipt };
}

for (const interrupt of ['SIGINT', 'SIGTERM']) {
  test(`${interrupt} reaps the owned group when the leader exits before its TERM-resistant descendant`, { timeout: 15000 }, async t => {
    const state = fixture(t, `import fs from 'node:fs'; import { spawn } from 'node:child_process';
const child = spawn(process.execPath, ['-e', "process.on('SIGTERM', () => {}); process.send(process.pid); setInterval(() => {}, 1000);"], { stdio: ['ignore', 'ignore', 'ignore', 'ipc'] });
child.once('message', pid => fs.writeFileSync(process.env.TEST_DESCENDANT_FILE, JSON.stringify({ leader: process.pid, descendant: pid })));
process.on('SIGTERM', () => process.exit(0));
`);
    const run = state.launch();
    await until(() => fs.existsSync(state.pids));
    const pids = JSON.parse(fs.readFileSync(state.pids));
    assert(alive(pids.descendant));
    run.master.kill(interrupt);
    assert.notEqual(await run.finished, 0, run.output());
    await until(() => !alive(pids.descendant));
    const result = state.receipt();
    assert.equal(result.interrupted, interrupt);
    assert.equal(result.completed, false);
    assert.equal(result.source_unchanged, true);
  });
}

test('a wrong uv version stops the real master before dependency acquisition', { timeout: 15000 }, async t => {
  const state = fixture(t, '// Synthetic prerequisite passes.\n');
  const uv = path.join(state.root, 'synthetic-uv');
  const acquired = path.join(state.root, 'unexpected-acquisition');
  fs.writeFileSync(uv, `#!/usr/bin/env node\nimport('node:fs').then(fs => { if (process.argv[2] === '--version') console.log('uv 0.13.6'); else fs.writeFileSync(${JSON.stringify(acquired)}, 'unexpected'); });\n`, { mode: 0o700 });
  const run = state.launch({ DEVSECOPS_TEST_UV: uv });
  assert.notEqual(await run.finished, 0, run.output());
  assert.equal(fs.existsSync(acquired), false);
  assert.match(state.receipt().failure, /requires uv 0\.13\.7/);
});

test('the pinned uv report permits build metadata and rejects extra output/version drift', () => {
  requireUvVersion('uv 0.13.7\n');
  requireUvVersion('uv 0.13.7 (synthetic-build 2026-11-12)\n');
  for (const text of ['uv 0.13.6\n', 'uv 0.13.70\n', 'uv 0.13.7\nextra\n', 'not uv'])
    assert.throws(() => requireUvVersion(text), /requires uv 0\.13\.7/);
});
