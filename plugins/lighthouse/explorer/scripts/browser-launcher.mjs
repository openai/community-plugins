import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFile, realpath } from 'node:fs/promises';
import { basename, dirname, join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { createPreviewServer } from './preview.mjs';

const host = '127.0.0.1';

function identity(marker) {
  const hash = createHash('sha256').update(marker).digest();
  return { token: hash.toString('hex'), port: 44000 + (hash.readUInt16BE() % 14000) };
}

function address(port) { return `http://${host}:${port}/?repo=benchmark`; }

async function status(port, token) {
  try {
    const response = await fetch(`http://${host}:${port}/__lighthouse/${token}`, { signal: AbortSignal.timeout(350) });
    if (!response.ok || await response.text() !== token) return { kind: 'other' };
    const pid = Number(response.headers.get('x-lighthouse-pid'));
    return { kind: 'ours', build: response.headers.get('x-lighthouse-build'), pid: Number.isSafeInteger(pid) && pid > 1 ? pid : null };
  } catch {
    return { kind: 'absent' };
  }
}

async function validateMarker(value) {
  if (!value) throw new Error('Supply the absolute path to a benchmark.lighthouse file.');
  const marker = await realpath(resolve(value));
  if (basename(marker) !== 'benchmark.lighthouse') throw new Error('The selected file must be named benchmark.lighthouse.');
  const data = JSON.parse(await readFile(marker, 'utf8'));
  if (data?.schemaVersion !== 1 || typeof data?.name !== 'string') throw new Error('This is not a Lighthouse version 1 benchmark marker.');
  return marker;
}

function widgetPath(command) {
  const adjacent = join(dirname(command), 'explorer.html');
  return existsSync(adjacent) ? adjacent : join(dirname(command), '../dist/explorer.html');
}

async function buildFingerprint(command) {
  const [code, widget] = await Promise.all([readFile(command), readFile(widgetPath(command))]);
  return createHash('sha256').update(code).update('\0').update(widget).digest('hex');
}

async function serve(marker, command, port, token, build) {
  const widget = await readFile(widgetPath(command));
  const server = createPreviewServer({ markers: new Map([['benchmark', marker]]), widget });
  const [handle] = server.listeners('request');
  if (typeof handle !== 'function') throw new Error('The Explorer HTTP handler is unavailable.');
  server.removeListener('request', handle);
  server.on('request', (request, response) => {
    if (request.url === `/__lighthouse/${token}`) {
      if (request.method !== 'GET' || request.headers.host !== `${host}:${port}`) {
        response.writeHead(403).end();
        return;
      }
      response.writeHead(200, { 'content-type': 'text/plain', 'cache-control': 'no-store', 'x-lighthouse-build': build, 'x-lighthouse-pid': String(process.pid) }).end(token);
      return;
    }
    handle.call(server, request, response);
  });
  await new Promise((complete, reject) => {
    server.once('error', reject);
    server.listen(port, host, complete);
  });
}

async function start(marker, command, port, token, build) {
  const initial = await status(port, token);
  if (initial.kind === 'ours' && initial.build === build) return address(port);
  if (initial.kind === 'other') throw new Error(`The Explorer's local port ${port} is already in use by another service.`);
  if (initial.kind === 'ours') {
    if (!initial.pid) throw new Error(`An earlier Explorer is still running on port ${port}. Stop that earlier preview before starting this version.`);
    try { process.kill(initial.pid); } catch (error) { if (error?.code !== 'ESRCH') throw error; }
    for (let attempt = 0; attempt < 20; attempt++) {
      if ((await status(port, token)).kind === 'absent') break;
      await delay(100);
    }
  }
  const subprocess = spawn(process.execPath, [command, '--serve', marker], { detached: true, stdio: 'ignore' });
  subprocess.once('error', () => {});
  subprocess.unref();
  for (let attempt = 0; attempt < 35; attempt++) {
    await delay(100);
    const current = await status(port, token);
    if (current.kind === 'ours' && current.build === build) return address(port);
  }
  throw new Error('The local Explorer did not start. Check that its bundled explorer.html exists and the local port is available.');
}

async function main() {
  const [first, second] = process.argv.slice(2);
  const serving = first === '--serve';
  if ((serving && !second) || (!serving && second)) throw new Error('Usage: node browser.cjs /absolute/path/to/benchmark.lighthouse');
  const marker = await validateMarker(serving ? second : first);
  const command = resolve(process.argv[1]);
  const { port, token } = identity(marker);
  const build = await buildFingerprint(command);
  if (serving) await serve(marker, command, port, token, build);
  else process.stdout.write(`${await start(marker, command, port, token, build)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : 'The Lighthouse Explorer could not start.'}\n`);
  process.exitCode = 1;
});
