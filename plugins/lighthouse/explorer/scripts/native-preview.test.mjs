import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import { after, before, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { Script } from 'node:vm';

let child;
let origin;
let root;

before(async () => {
  root = await mkdtemp(join(tmpdir(), 'lighthouse-native-preview-'));
  const marker = join(root, 'benchmark.lighthouse');
  await writeFile(marker, JSON.stringify({ schemaVersion: 1, name: 'Navigation fixture', cohorts: [], qualityIndexes: [] }));
  child = spawn(process.execPath, [fileURLToPath(new URL('./native-preview.mjs', import.meta.url)), marker], {
    env: { ...process.env, PORT: '0' }, stdio: ['ignore', 'ignore', 'pipe'],
  });
  origin = await new Promise((resolve, reject) => {
    let output = '';
    const deadline = setTimeout(() => reject(new Error(`Preview never started: ${output}`)), 10_000);
    const finish = (fn, value) => { clearTimeout(deadline); fn(value); };
    child.once('error', (error) => finish(reject, error));
    child.once('exit', (code) => finish(reject, new Error(`Preview exited ${code}: ${output}`)));
    child.stderr.on('data', (chunk) => {
      output += String(chunk);
      const match = output.match(/http:\/\/127\.0\.0\.1:\d+/);
      if (match) finish(resolve, match[0]);
    });
  });
});

after(async () => {
  if (child?.exitCode === null) {
    const closed = once(child, 'exit');
    child.kill();
    await closed;
  }
  if (root) await rm(root, { recursive: true, force: true });
});

async function shell(initialUrl = origin) {
  const response = await fetch(initialUrl);
  assert.equal(response.status, 200);
  const source = (await response.text()).match(/<script>([\s\S]*?)<\/script>/)?.[1];
  assert.ok(source);
  const listeners = new Map();
  const messages = [];
  const entries = [];
  let cursor = -1;
  let locationURL = new URL(initialUrl);
  const location = { get href() { return locationURL.href; }, get origin() { return locationURL.origin; } };
  const frame = { contentWindow: { postMessage(message) { messages.push(JSON.parse(JSON.stringify(message))); } } };
  const history = {
    replaceState(state, _unused, url) {
      locationURL = new URL(url);
      if (cursor < 0) cursor = 0;
      entries[cursor] = { state, url: locationURL.href };
    },
    pushState(state, _unused, url) {
      locationURL = new URL(url);
      entries.splice(cursor + 1, entries.length, { state, url: locationURL.href });
      cursor++;
    },
    async go(delta) {
      const target = cursor + delta;
      if (target < 0 || target >= entries.length) return;
      cursor = target;
      const entry = entries[cursor];
      locationURL = new URL(entry.url);
      await listeners.get('popstate')({ state: entry.state });
    },
    get length() { return entries.length; },
  };
  const window = { addEventListener(name, listener) { listeners.set(name, listener); } };
  new Script(source).runInNewContext({ document: { querySelector: () => frame }, window, history, location, URL });
  return {
    history,
    location,
    messages,
    async send(method, params, id = 1) {
      await listeners.get('message')({ source: frame.contentWindow, origin: location.origin, data: { jsonrpc: '2.0', method, params, id } });
      return messages.findLast((message) => message.id === id);
    },
  };
}

const self = (path) => `codex://plugins/self/app/lighthouse_explorer_home?path=${encodeURIComponent(path)}`;
const context = (url) => ({ theme: 'light', displayMode: 'inline', 'openai/deepLink': { url } });
const paths = (browser) => browser.messages.filter((message) => message.method === 'ui/notifications/host-context-changed').map((message) => message.params);

test('self links enter real shell browser history and back and forward notify the mounted widget', async () => {
  const browser = await shell();
  const initial = await browser.send('ui/initialize', {}, 1);
  assert.deepEqual(initial.result.hostCapabilities.experimental, { 'openai/selfDeepLinks': {} });
  assert.deepEqual(initial.result.hostContext, context('/'));
  assert.deepEqual(await browser.send('ui/open-link', { url: self('/benchmarks/a/tasks?pane=rubric') }, 2), { jsonrpc: '2.0', id: 2, result: { isError: false } });
  await browser.send('ui/open-link', { url: self('/benchmarks/a/reviews') }, 3);
  await browser.send('ui/open-link', { url: self('/benchmarks/a/reviews') }, 4);
  assert.equal(browser.history.length, 3);
  assert.equal(new URL(browser.location.href).searchParams.get('path'), '/benchmarks/a/reviews');
  await browser.history.go(-1);
  await browser.history.go(-1);
  assert.equal(new URL(browser.location.href).searchParams.has('path'), false);
  await browser.history.go(1);
  assert.deepEqual(paths(browser), [
    context('/benchmarks/a/tasks?pane=rubric'), context('/benchmarks/a/reviews'),
    context('/benchmarks/a/tasks?pane=rubric'), context('/'), context('/benchmarks/a/tasks?pane=rubric'),
  ]);
  await browser.send('ui/open-link', { url: self('/benchmarks/b') }, 5);
  assert.equal(browser.history.length, 3);
  await browser.history.go(1);
  assert.equal(paths(browser).at(-1)['openai/deepLink'].url, '/benchmarks/b');
});

test('deep startup routes survive reload while external and malformed links retain their prior behavior', async () => {
  const browser = await shell(`${origin}/?path=${encodeURIComponent('/benchmarks/a/tasks?pane=rubric')}`);
  const initial = await browser.send('ui/initialize', {}, 1);
  assert.deepEqual(initial.result.hostContext, context('/benchmarks/a/tasks?pane=rubric'));
  const other = [
    'https://example.com', 'codex://plugins/other/app/lighthouse_explorer_home?path=%2Fother',
    'codex://plugins/self/app/another?path=%2Fother', self('//example.com'), self('/view#hidden'), self('/%ZZ'), self('/raw space'),
    'codex://plugins/self/app/lighthouse_explorer_home?path=%2Fa&path=%2Fb',
  ];
  for (const [index, url] of other.entries()) {
    assert.deepEqual((await browser.send('ui/open-link', { url }, index + 2)).result, {});
  }
  assert.equal(browser.history.length, 1);
  assert.deepEqual(paths(browser), []);

  const library = await fetch(`${origin}/call`, {
    method: 'POST', headers: { origin, 'content-type': 'application/json' },
    body: JSON.stringify({ name: 'lighthouse_explorer_library', arguments: {} }),
  });
  assert.equal(library.status, 200);
  assert.equal((await library.json()).structuredContent.benchmarks[0].name, 'Navigation fixture');
});

test('thread preview mounts the actual fullscreen resource and focuses only a configured benchmark alias', async () => {
  const alias = basename(root);
  const browser = await shell(`${origin}/?surface=thread&benchmark=${alias}&task=draft-1&pane=rubric`);
  const initial = await browser.send('ui/initialize', {}, 1);
  assert.equal(initial.result.hostContext.displayMode, 'fullscreen');
  await browser.send('ui/notifications/initialized', {}, 2);
  const launch = browser.messages.find((message) => message.method === 'ui/notifications/tool-input');
  assert.deepEqual(launch.params.arguments, {benchmarkPath: join(root, 'benchmark.lighthouse'), taskId: 'draft-1', pane: 'rubric'});

  const thread = await fetch(`${origin}/widget?surface=thread`);
  assert.match(await thread.text(), /<html data-lighthouse-surface="thread"/);
  const global = await fetch(`${origin}/widget`);
  assert.match(await global.text(), /<html data-lighthouse-surface="home"/);
  assert.equal((await fetch(`${origin}/?surface=thread&benchmark=unconfigured&task=draft-1`)).status, 400);
  assert.equal((await fetch(`${origin}/?surface=thread&benchmark=${alias}&pane=not-a-pane`)).status, 400);

  const request = (name, args) => fetch(`${origin}/call`, {method: 'POST', headers: {origin, 'content-type': 'application/json'}, body: JSON.stringify({name, arguments: args})});
  const attached = await request('lighthouse_explorer_attach', {path: join(root, 'benchmark.lighthouse')});
  assert.equal(attached.status, 200);
  const benchmark = (await attached.json()).structuredContent.benchmark;
  const snapshot = await request('lighthouse_explorer_authoring_snapshot', {benchmarkId: benchmark.id, taskId: 'draft-1'});
  assert.equal(snapshot.status, 200);
  assert.equal((await snapshot.json()).structuredContent.benchmark.name, 'Navigation fixture');
  assert.equal((await request('lighthouse_explorer_attach', {path: '/private/unconfigured/benchmark.lighthouse'})).status, 400);
});
