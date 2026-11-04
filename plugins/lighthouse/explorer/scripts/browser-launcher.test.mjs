import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFile, spawn } from 'node:child_process';
import { appendFile, copyFile, mkdir, mkdtemp, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const run = promisify(execFile);
const launcher = fileURLToPath(new URL('../dist/browser.cjs', import.meta.url));
const widgetFile = fileURLToPath(new URL('../dist/explorer.html', import.meta.url));

test('the installed-shape browser launcher opens the dashboard rather than the JSON marker and reuses its viewer', async () => {
  const root = await mkdtemp(join(tmpdir(), 'lighthouse-browser-'));
  const marker = join(root, 'benchmark.lighthouse');
  await writeFile(marker, JSON.stringify({ schemaVersion: 1, name: 'Dashboard contract', cohorts: [], qualityIndexes: [] }));
  const child = spawn(process.execPath, [launcher, '--serve', marker], { stdio: 'pipe' });
  const output = [];
  child.stderr.on('data', (data) => output.push(data.toString()));
  let replacementPid;
  try {
    const hash = createHash('sha256').update(await realpath(marker)).digest();
    const port = 44000 + (hash.readUInt16BE() % 14000);
    const origin = `http://127.0.0.1:${port}`;
    let ready = false;
    for (let tries = 0; tries < 35 && !ready; tries++) {
      try { ready = (await fetch(`${origin}/__lighthouse/${hash.toString('hex')}`)).ok; } catch { await delay(50); }
    }
    assert.ok(ready, output.join(''));
    for (let times = 0; times < 2; times++) {
      const result = await run(process.execPath, [launcher, marker]);
      assert.equal(result.stdout.trim(), `${origin}/?repo=benchmark`);
    }
    const dashboard = await fetch(`${origin}/?repo=benchmark`);
    assert.match(dashboard.headers.get('content-type'), /text\/html/);
    assert.match(await dashboard.text(), /<iframe[^>]*Lighthouse Explorer/);
    const state = await fetch(`${origin}/snapshot?repo=benchmark`).then((response) => response.json());
    assert.equal(state.benchmark.name, 'Dashboard contract');
    const widget = await fetch(`${origin}/widget`).then((response) => response.text());
    assert.match(widget, /Tasks &amp; rubrics|Tasks & rubrics/);

    const refreshed = join(root, 'new-bundle');
    await mkdir(refreshed);
    const newLauncher = join(refreshed, 'browser.cjs');
    await copyFile(launcher, newLauncher);
    await copyFile(widgetFile, join(refreshed, 'explorer.html'));
    await appendFile(newLauncher, '\n// A newly installed plugin revision.\n');
    const previousHealth = await fetch(`${origin}/__lighthouse/${hash.toString('hex')}`);
    const replacement = await run(process.execPath, [newLauncher, marker]);
    assert.equal(replacement.stdout.trim(), `${origin}/?repo=benchmark`);
    const currentHealth = await fetch(`${origin}/__lighthouse/${hash.toString('hex')}`);
    replacementPid = Number(currentHealth.headers.get('x-lighthouse-pid'));
    assert.notEqual(replacementPid, child.pid);
    assert.notEqual(currentHealth.headers.get('x-lighthouse-build'), previousHealth.headers.get('x-lighthouse-build'));
  } finally {
    child.kill();
    if (replacementPid) process.kill(replacementPid);
    await rm(root, { recursive: true, force: true });
  }
});

test('browser launcher rejects an arbitrary JSON file before starting a viewer', async () => {
  const root = await mkdtemp(join(tmpdir(), 'lighthouse-browser-'));
  try {
    const path = join(root, 'secret.json');
    await writeFile(path, JSON.stringify({ schemaVersion: 1, name: 'Wrong file' }));
    await assert.rejects(run(process.execPath, [launcher, path]), /must be named benchmark\.lighthouse/);
  } finally { await rm(root, { recursive: true, force: true }); }
});
