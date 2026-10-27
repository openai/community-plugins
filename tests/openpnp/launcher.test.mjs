// SPDX-License-Identifier: Apache-2.0
// Executable and JAR fixtures below exercise installer behavior only; they are not OpenPnP simulators.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, writeFile, readFile, readdir, chmod, symlink, rm, access } from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { atomicJson, installBridge, launchSimulator } from '../../plugins/openpnp/scripts/openpnp.mjs';

const upstream = '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const absent = async file => assert.rejects(access(file), { code: 'ENOENT' });

async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-launcher-fixture-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateDir = path.join(root, 'state');
  const sourceRoot = path.join(root, 'plugin');
  const openpnpHome = path.join(root, 'distribution');
  await mkdir(path.join(sourceRoot, 'bridge'), { recursive: true });
  const bridge = Buffer.from('installer-only bridge bytes, not Java');
  await writeFile(path.join(sourceRoot, 'bridge/openpnp-codex-bridge.jar'), bridge);
  const entries = [
    ['openpnp-gui.jar', 'installer-only application bytes'],
    ['lib/verified.jar', 'installer-only library bytes'],
    ['samples/pnp-test/board.xml', 'installer-only sample bytes'],
  ];
  for (const [relative, content] of entries) {
    await mkdir(path.dirname(path.join(openpnpHome, relative)), { recursive: true });
    await writeFile(path.join(openpnpHome, relative), content);
  }
  const manifest = { upstream_commit: upstream, gui_jar: 'openpnp-gui.jar', libs_directory: 'lib', samples_directory: 'samples',
    files: entries.map(([relative, content]) => ({ path: relative, sha256: hash(content) })) };
  // Fixture provenance is deliberately bound to this exact synthetic runtime,
  // just as a real bridge build records its native runtime and application hashes.
  async function saveManifest() {
    const manifestPath = path.join(openpnpHome, 'codex-build-manifest.json');
    await atomicJson(manifestPath, manifest);
    await atomicJson(path.join(sourceRoot, 'bridge/build-manifest.json'), {
      upstream_commit: upstream, bridge_version: '0.1.0', bridge_sha256: hash(bridge),
      runtime_manifest_sha256: hash(await readFile(manifestPath)),
      patched_native_jar_sha256: hash(entries[0][1]),
    });
    await installBridge(stateDir, sourceRoot);
  }
  await saveManifest();
  const marker = path.join(root, 'executable-arguments.json');
  async function executable(body = '') {
    const file = path.join(root, 'installer-only-executable');
    await writeFile(file, `#!${process.execPath}\n// Installer fixture, not a Java or OpenPnP implementation.\n` +
      `require('node:fs').writeFileSync(${JSON.stringify(marker)}, JSON.stringify(process.argv.slice(2)));\n${body}\n`);
    await chmod(file, 0o700);
    return file;
  }
  return { root, stateDir, sourceRoot, openpnpHome, manifest, marker, executable,
    saveManifest };
}

async function rpcFixture(t, handler) {
  const server = http.createServer(handler);
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  return server.address().port;
}

test('launcher rejects a zero exit before readiness and leaves no connection receipt', async t => {
  const f = await fixture(t);
  const java = await f.executable('process.exit(0);');
  await assert.rejects(launchSimulator({ ...f, java }), /before readiness was verified/);
  await absent(path.join(f.stateDir, 'connection.json'));
});

test('launcher verifies fragmented readiness using authenticated RPC before creating connection metadata', async t => {
  const f = await fixture(t);
  let authenticated = false;
  const port = await rpcFixture(t, async (request, response) => {
    try {
      await absent(path.join(f.stateDir, 'connection.json'));
      const token = (await readFile(path.join(f.stateDir, 'bridge.token'), 'utf8')).trim();
      assert.equal(request.headers.authorization, `Bearer ${token}`);
      let body = ''; for await (const chunk of request) body += chunk;
      assert.deepEqual(JSON.parse(body), { method: 'openpnp_get_capabilities', params: {} });
      authenticated = true;
      response.setHeader('content-type', 'application/json');
      response.end(JSON.stringify({ result: { upstream_commit: upstream, schema_version: 1, bridge_version: '0.1.0',
        machine_id: 'installer-fixture-only', simulation: true, hardware_qualified: false } }));
    } catch (error) { response.writeHead(500); response.end(error.message); }
  });
  const java = await f.executable(`process.stdout.write('OPENPNP_CODEX_RE');
setTimeout(() => process.stdout.write('ADY port=${port} upstream=${upstream} mode=native-simulator\\n'), 15);
setTimeout(() => process.exit(0), 300);`);
  await launchSimulator({ ...f, java });
  assert.equal(authenticated, true);
  const connection = JSON.parse(await readFile(path.join(f.stateDir, 'connection.json'), 'utf8'));
  assert.equal(connection.url, `http://127.0.0.1:${port}/`);
  assert.equal(connection.machineId, 'installer-fixture-only');
  assert.equal(connection.tokenFile, path.join(f.stateDir, 'bridge.token'));
  assert.ok(!Object.hasOwn(connection, 'token'));
});

test('readiness followed by rejected authentication fails instead of reporting successful launch', async t => {
  const f = await fixture(t);
  const port = await rpcFixture(t, (_request, response) => { response.writeHead(403); response.end('fixture denial'); });
  const java = await f.executable(`console.log('OPENPNP_CODEX_READY port=${port} upstream=${upstream} mode=native-simulator');
setInterval(() => {}, 1000);`);
  await assert.rejects(launchSimulator({ ...f, java }), /exited unexpectedly \(SIGTERM\)/);
  await absent(path.join(f.stateDir, 'connection.json'));
});

async function launchCliFixture(t, f, java) {
  // The outer CLI runs separately so tests signal only their own child processes, never the test runner.
  const cli = fileURLToPath(new URL('../../plugins/openpnp/scripts/openpnp.mjs', import.meta.url));
  const child = spawn(process.execPath, [cli, 'start-simulator', '--state-dir', f.stateDir,
    '--openpnp-home', f.openpnpHome, '--java', java], { stdio: ['ignore', 'pipe', 'pipe'], shell: false });
  let stderr = ''; let pending = ''; let readyObserved = false; let fixturePid;
  let resolveReady; let rejectReady;
  const ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
  const killFixture = () => {
    if (!fixturePid) return;
    try { process.kill(fixturePid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
  };
  const timer = setTimeout(() => { killFixture(); child.kill('SIGKILL'); }, 10_000);
  child.stderr.on('data', chunk => { stderr += chunk.toString(); });
  child.stdout.on('data', chunk => {
    pending += chunk.toString();
    let newline;
    while ((newline = pending.indexOf('\n')) !== -1) {
      const line = pending.slice(0, newline); pending = pending.slice(newline + 1);
      let value; try { value = JSON.parse(line); } catch { continue; }
      if (value.connected === true && Number.isInteger(value.simulator_pid)) {
        readyObserved = true; fixturePid = value.simulator_pid; resolveReady(value);
      }
    }
  });
  const completion = new Promise((resolve, reject) => {
    child.once('error', error => { clearTimeout(timer); rejectReady(error); reject(error); });
    child.once('close', (code, signal) => {
      clearTimeout(timer);
      if (!readyObserved) rejectReady(new Error(`Fixture CLI exited before verified readiness: ${stderr}`));
      resolve({ code, signal, stderr });
    });
  });
  t.after(async () => {
    clearTimeout(timer);
    if (child.exitCode === null && child.signalCode === null) { killFixture(); child.kill('SIGKILL'); }
    await completion;
  });
  return { child, completion, receipt: await ready };
}

test('CLI distinguishes forwarded intentional stops from unexpected child signals and exit 143', async t => {
  for (const mode of ['forwarded-SIGINT', 'forwarded-SIGTERM', 'unexpected-SIGTERM', 'unrequested-143']) {
    await t.test(mode, async subtest => {
      const f = await fixture(subtest);
      const port = await rpcFixture(subtest, (_request, response) => {
        response.setHeader('content-type', 'application/json');
        response.end(JSON.stringify({ result: { upstream_commit: upstream, schema_version: 1, bridge_version: '0.1.0',
          machine_id: 'installer-stop-fixture-only', simulation: true, hardware_qualified: false } }));
      });
      const stopReceipt = path.join(f.root, 'forwarded-stop-received');
      const behavior = mode.startsWith('forwarded-')
        ? `process.once('SIGTERM', () => { require('node:fs').writeFileSync(${JSON.stringify(stopReceipt)}, 'SIGTERM then exit 143'); process.exit(143); });`
        : mode === 'unrequested-143' ? `process.once('SIGUSR2', () => process.exit(143));` : '';
      const java = await f.executable(`${behavior}\nconsole.log('OPENPNP_CODEX_READY port=${port} upstream=${upstream} mode=native-simulator');\nsetInterval(() => {}, 1000);`);
      const launched = await launchCliFixture(subtest, f, java);
      if (mode.startsWith('forwarded-')) launched.child.kill(mode.slice('forwarded-'.length));
      else process.kill(launched.receipt.simulator_pid, mode === 'unrequested-143' ? 'SIGUSR2' : 'SIGTERM');
      const exit = await launched.completion;
      assert.equal(exit.signal, null, 'The CLI handles the signal and chooses its own exit status.');
      if (mode.startsWith('forwarded-')) {
        assert.equal(exit.code, 0, exit.stderr);
        assert.equal(await readFile(stopReceipt, 'utf8'), 'SIGTERM then exit 143');
        assert.ok(!exit.stderr.includes('exited unexpectedly'));
      } else {
        assert.equal(exit.code, 1, 'An unsolicited child exit must not be accepted as an intentional CLI stop.');
        assert.match(exit.stderr, mode === 'unrequested-143' ? /exited unexpectedly \(code 143\)/ : /exited unexpectedly \(SIGTERM\)/);
      }
    });
  }
});

test('runtime inventory rejects missing application hashes, extra sample files, and escaped files before spawning', async t => {
  for (const kind of ['missing-gui', 'extra-sample', 'escaped-file', 'sample-symlink']) {
    await t.test(kind, async subtest => {
      const f = await fixture(subtest);
      const java = await f.executable('process.exit(0);');
      let expected;
      if (kind === 'missing-gui') {
        f.manifest.files = f.manifest.files.filter(entry => entry.path !== f.manifest.gui_jar);
        expected = /application JAR is not in the verified manifest/;
      } else if (kind === 'extra-sample') {
        await writeFile(path.join(f.openpnpHome, 'samples/pnp-test/unlisted.xml'), 'unlisted');
        expected = /outside the verified inventory/;
      } else if (kind === 'escaped-file') {
        const outside = path.join(f.root, 'external-library.jar');
        await writeFile(outside, 'external fixture bytes');
        await symlink(outside, path.join(f.openpnpHome, 'lib/escape.jar'));
        f.manifest.files.push({ path: 'lib/escape.jar', sha256: hash('external fixture bytes') });
        expected = /escapes its distribution/;
      } else {
        await symlink('board.xml', path.join(f.openpnpHome, 'samples/pnp-test/linked.xml'));
        expected = /symbolic links/;
      }
      await f.saveManifest();
      await assert.rejects(launchSimulator({ ...f, java }), expected);
      await absent(f.marker);
    });
  }
});

test('launcher classpath includes verified JARs explicitly and excludes unlisted libraries', async t => {
  const f = await fixture(t);
  await writeFile(path.join(f.openpnpHome, 'lib/unlisted.jar'), 'installer-only unexpected library');
  const java = await f.executable('process.exit(0);');
  await assert.rejects(launchSimulator({ ...f, java }), /before readiness was verified/);
  const args = JSON.parse(await readFile(f.marker, 'utf8'));
  const classpath = args[args.indexOf('-cp') + 1];
  assert.deepEqual(classpath.split(path.delimiter), [
    path.join(f.stateDir, 'bridge/0.1.0/openpnp-codex-bridge.jar'), path.join(f.openpnpHome, 'openpnp-gui.jar'),
    path.join(f.openpnpHome, 'lib/verified.jar'),
  ]);
  assert.ok(!classpath.includes('*') && !classpath.includes('unlisted.jar'));
});

test('runtime cannot bless changed application bytes by rewriting its own inventory', async t => {
  const f = await fixture(t), java = await f.executable('process.exit(0);');
  const altered = 'different application bytes outside the installed build';
  await writeFile(path.join(f.openpnpHome, f.manifest.gui_jar), altered);
  f.manifest.files.find(entry => entry.path === f.manifest.gui_jar).sha256 = hash(altered);
  // Do not update the bridge build: an untrusted distribution cannot authorize itself.
  await atomicJson(path.join(f.openpnpHome, 'codex-build-manifest.json'), f.manifest);
  await assert.rejects(launchSimulator({ ...f, java }), /manifest does not match the installed bridge build/);
  await absent(f.marker);
});

test('installation receipt failure keeps a complete immutable JAR and permits verified recovery', async t => {
  const f = await fixture(t);
  const state = path.join(f.root, 'failed-installation');
  const receipt = path.join(state, 'installation.json');
  await mkdir(receipt, { recursive: true });
  await writeFile(path.join(receipt, 'obstruction'), 'intentional installer-only filesystem fault');
  await mkdir(path.join(state, 'journal'), { recursive: true });
  await writeFile(path.join(state, 'journal/history'), 'retained');
  await assert.rejects(installBridge(state, f.sourceRoot));
  assert.equal(await readFile(path.join(state, 'bridge/0.1.0/openpnp-codex-bridge.jar'), 'utf8'), 'installer-only bridge bytes, not Java');
  assert.deepEqual(await readdir(path.join(state, 'staging')), []);
  assert.ok(!(await readdir(state)).some(name => name.endsWith('.tmp')));
  await rm(receipt, { recursive: true });
  assert.equal((await installBridge(state, f.sourceRoot)).installed, true);
  assert.equal(await readFile(path.join(state, 'journal/history'), 'utf8'), 'retained');
});

test('concurrent installation publishes matching complete bytes without abandoned staging files', async t => {
  const f = await fixture(t);
  const state = path.join(f.root, 'concurrent-installation');
  const results = await Promise.all([installBridge(state, f.sourceRoot), installBridge(state, f.sourceRoot)]);
  assert.ok(results.every(result => result.installed));
  const installed = await readFile(results[0].bridge_jar);
  assert.equal(installed.toString(), 'installer-only bridge bytes, not Java');
  const receipt = JSON.parse(await readFile(path.join(state, 'installation.json'), 'utf8'));
  assert.equal(hash(installed), receipt.bridge_sha256);
  assert.deepEqual(await readdir(path.join(state, 'staging')), []);
});
