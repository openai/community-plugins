import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, symlink, writeFile } from 'node:fs/promises';
import { request } from 'node:http';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { after, before, test } from 'node:test';
import { Script } from 'node:vm';
import { createPreviewServer, parseMarkers } from './preview.mjs';

let root;
let origin;
let port;
let server;

before(async () => {
  root = await mkdtemp(join(tmpdir(), 'lighthouse-browser-'));
  const markers = new Map();
  for (const repo of ['polars', 'grimoire']) {
    const benchmark = join(root, repo);
    await mkdir(join(benchmark, 'review/demo'), { recursive: true });
    const marker = join(benchmark, 'benchmark.lighthouse');
    await writeFile(marker, '{}');
    await writeFile(join(benchmark, 'review/demo/rubric.md'), `${repo}\n<script>alert('example')</script>`);
    await writeFile(join(benchmark, '.env'), `${repo} private contents`);
    markers.set(repo, marker);
  }
  await symlink(join(root, 'grimoire/review/demo/rubric.md'), join(root, 'polars/review/demo/escape.md'));
  server = createPreviewServer({ markers, widget: '<p>widget</p>', snapshot: async (marker) => ({ name: basename(dirname(marker)) }) });
  await new Promise((ready) => server.listen(0, '127.0.0.1', ready));
  port = server.address().port;
  origin = `http://127.0.0.1:${port}`;
});

after(async () => {
  if (server) await new Promise((done) => server.close(done));
  if (root) await rm(root, { recursive: true, force: true });
});

test('browser opens the dashboard with direct benchmark navigation and working host script', async () => {
  const home = await fetch(origin, { redirect: 'manual' });
  assert.equal(home.status, 302);
  assert.equal(home.headers.get('location'), '/?repo=polars');
  const response = await fetch(`${origin}/?repo=grimoire`);
  const html = await response.text();
  assert.equal(response.status, 200);
  assert.match(html, /<title>Grimoire · Lighthouse Explorer<\/title>/);
  assert.match(html, /href="\/\?repo=polars"/);
  assert.match(html, /href="\/\?repo=grimoire" aria-current="page"/);
  assert.match(html, /<iframe id="dashboard" title="Lighthouse Explorer" src="\/widget">/);
  assert.match(html, /<dialog id="evidence-dialog"/);
  assert.doesNotMatch(html, /simulator|native file launch/);
  const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
  assert.ok(script);
  assert.doesNotThrow(() => new Script(script));
  assert.equal(await (await fetch(`${origin}/widget`)).text(), '<p>widget</p>');
  assert.equal((await fetch(`${origin}/?repo=third`)).status, 404);
});

test('snapshot and source evidence retain the active benchmark boundary', async () => {
  assert.deepEqual(await (await fetch(`${origin}/snapshot?repo=polars`)).json(), { name: 'polars' });
  assert.deepEqual(await (await fetch(`${origin}/snapshot?repo=grimoire`)).json(), { name: 'grimoire' });
  assert.equal((await fetch(`${origin}/snapshot?repo=third`)).status, 404);
  const path = 'review/demo/rubric.md';
  const evidence = (repo, selected = path, endpoint = 'evidence') => `${origin}/${endpoint}?repo=${repo}&path=${encodeURIComponent(selected)}`;
  const resolution = await fetch(evidence('polars', path, 'resolve-evidence'));
  assert.deepEqual(await resolution.json(), { path });
  const document = await fetch(evidence('polars'));
  assert.equal(document.status, 200);
  assert.match(document.headers.get('content-security-policy'), /default-src 'none'/);
  const html = await document.text();
  assert.match(html, /polars\n&lt;script&gt;alert\('example'\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>|grimoire/);
  assert.match(await (await fetch(evidence('grimoire'))).text(), /grimoire/);
  for (const selected of ['.env', '../grimoire/review/demo/rubric.md', 'review/demo/escape.md', '/etc/passwd', '/etc/example.png']) {
    assert.equal((await fetch(evidence('polars', selected))).status, 403);
    assert.equal((await fetch(evidence('polars', selected, 'resolve-evidence'))).status, 403);
  }
  assert.equal((await fetch(evidence('polars', 'review/demo/missing.md'))).status, 404);
});

test('local read-only viewer rejects rebinding and cross-origin data requests', async () => {
  const crossOrigin = await fetch(`${origin}/snapshot?repo=polars`, { headers: { origin: 'https://example.org' } });
  assert.equal(crossOrigin.status, 403);
  assert.equal(crossOrigin.headers.get('access-control-allow-origin'), null);
  assert.equal((await fetch(`${origin}/snapshot?repo=polars`, { method: 'POST' })).status, 405);
  const externalHost = await new Promise((done, reject) => {
    const connection = request({ hostname: '127.0.0.1', port, path: '/snapshot?repo=polars', headers: { host: `attacker.example:${port}` } }, (response) => {
      response.resume();
      response.on('end', () => done(response.statusCode));
    });
    connection.on('error', reject);
    connection.end();
  });
  assert.equal(externalHost, 403);
});

test('launcher accepts only explicit absolute benchmark mappings', () => {
  assert.deepEqual([...parseMarkers(['polars=/bench/benchmark.lighthouse'])], [['polars', '/bench/benchmark.lighthouse']]);
  for (const invalid of [[], ['bad'], ['bad repo=/bench/file'], ['polars=relative/file'], ['polars=/one', 'polars=/two']]) {
    assert.throws(() => parseMarkers(invalid));
  }
});

test('single-benchmark launcher labels its Codex browser tab with the actual repository', async () => {
  const single = createPreviewServer({ markers: new Map([['benchmark', join(root, 'grimoire-bench-3/benchmark.lighthouse')]]), widget: '' });
  await new Promise((ready) => single.listen(0, '127.0.0.1', ready));
  try {
    const response = await fetch(`http://127.0.0.1:${single.address().port}/?repo=benchmark`);
    const html = await response.text();
    assert.match(html, /<title>Grimoire Bench 3 · Lighthouse Explorer<\/title>/);
    assert.match(html, /aria-current="page">Grimoire Bench 3<\/a>/);
  } finally {
    await new Promise((done) => single.close(done));
  }
});
