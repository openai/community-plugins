// SPDX-License-Identifier: Apache-2.0
// Installer/executable/HTTP fixtures only. These are not native OpenPnP or GUI qualification tests.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, writeFile, readFile, readdir, chmod, symlink, rm, access, stat, realpath } from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { atomicJson, installBridge, parseArguments } from '../../plugins/openpnp/scripts/openpnp.mjs';
import { prepareGuiSimulator, launchGuiSimulator } from '../../plugins/openpnp/scripts/gui-launcher.mjs';

const upstream = '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c';
const hash = data => createHash('sha256').update(data).digest('hex');
const cli = fileURLToPath(new URL('../../plugins/openpnp/scripts/openpnp.mjs', import.meta.url));
const absent = file => assert.rejects(access(file), { code: 'ENOENT' });
const property = (args, key) => args.find(value => value.startsWith(`-D${key}=`))?.slice(key.length + 3);

async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-gui-launcher-fixture-')); t.after(() => rm(root, { recursive: true, force: true }));
  const stateDir = path.join(root, 'state'), sourceRoot = path.join(root, 'plugin'), openpnpHome = path.join(root, 'runtime');
  await mkdir(path.join(sourceRoot, 'bridge'), { recursive: true });
  const bridge = Buffer.from('GUI installer fixture bridge; not Java');
  const bootstrap = await readFile(new URL('../../plugins/openpnp/bridge/bootstrap.js', import.meta.url));
  await writeFile(path.join(sourceRoot, 'bridge/openpnp-codex-bridge.jar'), bridge); await writeFile(path.join(sourceRoot, 'bridge/bootstrap.js'), bootstrap);
  const entries = [['openpnp-gui.jar', 'fixture patched native JAR'], ['gui-preferences.jar', 'fixture preferences-only JAR'], ['lib/a.jar', 'fixture library A'], ['lib/b.jar', 'fixture library B'], ['samples/pnp-test/board.xml', 'fixture board']];
  for (const [name, data] of entries) { await mkdir(path.dirname(path.join(openpnpHome, name)), { recursive: true }); await writeFile(path.join(openpnpHome, name), data); }
  const manifest = { upstream_commit: upstream, gui_jar: 'openpnp-gui.jar', gui_launcher_jar: 'gui-preferences.jar', libs_directory: 'lib', samples_directory: 'samples', gui_ownership: { api_version: 1, patch_id: 'codex-gui-ownership-v1', patch_sha256: 'a'.repeat(64) }, native_action_observer: { api_version: 1, patch_sha256: 'b'.repeat(64) }, files: entries.map(([name, data]) => ({ path: name, sha256: hash(data) })) };
  await atomicJson(path.join(openpnpHome, 'codex-build-manifest.json'), manifest);
  const build = { upstream_commit: upstream, bridge_version: '0.1.0', bridge_sha256: hash(bridge), bootstrap_sha256: hash(bootstrap), patched_native_jar_sha256: hash(entries[0][1]), runtime_manifest_sha256: hash(await readFile(path.join(openpnpHome, 'codex-build-manifest.json'))) };
  await atomicJson(path.join(sourceRoot, 'bridge/build-manifest.json'), build); await installBridge(stateDir, sourceRoot);
  const marker = path.join(root, 'executable.json');
  async function executable(body = 'process.exit(0);') {
    const file = path.join(root, 'installer-only-gui-executable');
    await writeFile(file, `#!${process.execPath}\n// Installer fixture, never a Java or native GUI implementation.\nconst fs=require('node:fs'),path=require('node:path');const args=process.argv.slice(2);fs.writeFileSync(${JSON.stringify(marker)},JSON.stringify({args,env:{JAVA_TOOL_OPTIONS:process.env.JAVA_TOOL_OPTIONS,CLASSPATH:process.env.CLASSPATH}}));\nconst nativeState=args.find(x=>x.startsWith('-Dopenpnp.codex.stateDir=')).split('=').slice(1).join('=');\n${body}\n`); await chmod(file, 0o700); return file;
  }
  return { root, stateDir, sourceRoot, openpnpHome, manifest, build, marker, executable,
    async repin() { await atomicJson(path.join(openpnpHome, 'codex-build-manifest.json'), manifest); build.runtime_manifest_sha256 = hash(await readFile(path.join(openpnpHome, 'codex-build-manifest.json'))); await atomicJson(path.join(stateDir, 'bridge/0.1.0/build-manifest.json'), build); } };
}
async function sensingExecutable(f, guiBody = 'process.exit(0);', prepareFailure = false) {
  const executable = path.join(f.root, 'sensing-installer-only-executable');
  const body = `#!${process.execPath}\nconst fs=require('node:fs'),path=require('node:path'),args=process.argv.slice(2);\nif(args.includes('org.openpnp.codex.GuiSensingFixture')) {\n fs.writeFileSync(${JSON.stringify(f.marker)},JSON.stringify({args,stage:'preparer'}));\n if(${prepareFailure}) process.exit(7);\n const config=args[args.indexOf('--config-dir')+1],manifest=args[args.indexOf('--manifest')+1];\n if(JSON.stringify(fs.readdirSync(config))!==JSON.stringify(['scripts'])||fs.readdirSync(path.join(config,'scripts')).length)throw Error('preparer needs empty configuration files');\n if((fs.statSync(path.join(config,'scripts')).mode&0o777)!==0o700)throw Error('scripts must be private before native preparation');\n fs.writeFileSync(path.join(config,'machine.xml'),'fixture only; no native source authority');\n fs.writeFileSync(manifest,JSON.stringify({fixture_only:true,scenario:args[args.indexOf('--scenario')+1]}),{mode:0o600});\n process.exit(0);\n}\nconst nativeState=args.find(x=>x.startsWith('-Dopenpnp.codex.stateDir=')).split('=').slice(1).join('=');\n${guiBody}\n`;
  await writeFile(executable, body); await chmod(executable, 0o700); return executable;
}

async function rpc(t, handler) { const server = http.createServer(handler); await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); }); t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }); return server.address().port; }
function capability(f, overrides = {}) { return { schema_version: 1, bridge_version: '0.1.0', upstream_commit: upstream, simulator_profile: 'gui-simulator', machine_id: 'installer-fixture-only', simulation: true, hardware_qualified: false, bridge_artifact_sha256: f.build.bridge_sha256, gui_ownership: { local_grant: false, gui_qualification: 'installer-fixture-unqualified' }, ...overrides }; }
function readyBody(port, delay = 500) { return `fs.writeFileSync(path.join(nativeState,'bridge.token'),'${'A'.repeat(43)}',{mode:0o600});process.stdout.write('2026 native INFO: OPENPNP_CODEX_GUI_');process.stderr.write('independent stderr line\\n');setTimeout(()=>process.stdout.write('READY port=${port} mode=gui-simulator\\n'),15);setTimeout(()=>process.exit(0),${delay});`; }

test('GUI command permits only a new simulator launch, never existing configuration, auto-grant or raw JVM arguments', () => {
  assert.equal(parseArguments(['start-gui-simulator', '--openpnp-home', '/runtime']).command, 'start-gui-simulator');
  for (const flag of ['--config-dir', '--auto-grant', '--java-options', '--port']) assert.throws(() => parseArguments(['start-gui-simulator', flag, '/existing']), /Unknown/);
});

test('preparation creates new private roots with exact bootstrap/runtime hashes, native-only classpath and isolated in-memory preferences', async t => {
  const f = await fixture(t); await writeFile(path.join(f.stateDir, 'user-script.js'), 'retained user script');
  await writeFile(path.join(f.openpnpHome, 'lib/unlisted.jar'), 'must not join classpath');
  const previous = process.env.JAVA_TOOL_OPTIONS; process.env.JAVA_TOOL_OPTIONS = '-DconfigDir=/untrusted';
  let first, second; try { first = await prepareGuiSimulator(f); second = await prepareGuiSimulator(f); } finally { if (previous === undefined) delete process.env.JAVA_TOOL_OPTIONS; else process.env.JAVA_TOOL_OPTIONS = previous; }
  assert.notEqual(first.session_root, second.session_root); assert.equal(first.connected, false); assert.equal(first.local_grant, false); assert.equal(first.environment.JAVA_TOOL_OPTIONS, undefined);
  assert.equal(first.args.at(-1), 'org.openpnp.Main'); assert.ok(first.args.includes('-Djava.awt.headless=false')); assert.ok(first.args.includes('-Xmx2g')); assert.ok(first.args.includes('-XX:+ExitOnOutOfMemoryError'));
  const nativeRoot = await realpath(f.openpnpHome);
  assert.deepEqual(first.args[first.args.indexOf('-cp') + 1].split(path.delimiter), ['gui-preferences.jar','openpnp-gui.jar','lib/a.jar','lib/b.jar'].map(name => path.join(nativeRoot, name)));
  assert.equal(property(first.args, 'java.util.prefs.PreferencesFactory'), 'org.openpnp.codex.IsolatedPreferencesFactory'); assert.match(first.preferences_scope, /in-memory/);
  assert.ok(property(first.args, 'user.home').startsWith(first.session_root)); assert.equal(property(first.args, 'configDir'), first.config_directory);
  assert.equal(hash(await readFile(first.bootstrap_script)), first.bootstrap_sha256); assert.equal(property(first.args, 'openpnp.codex.runtimeManifestSha256'), f.build.runtime_manifest_sha256);
  assert.equal((await stat(first.config_directory)).mode & 0o777, 0o700); assert.equal((await stat(first.bootstrap_script)).mode & 0o777, 0o600);
  await absent(path.join(first.config_directory, 'machine.xml')); await absent(path.join(first.config_directory, 'scripts/Examples')); await absent(path.join(f.stateDir, 'connection.json')); await absent(f.marker);
  assert.equal(await readFile(path.join(f.stateDir, 'user-script.js'), 'utf8'), 'retained user script');
});

test('bootstrap/runtime/bridge/provenance tampering and unknown samples reject before executable or session creation', async t => {
  for (const kind of ['bootstrap', 'bridge', 'runtime', 'ownership', 'extra-sample', 'escape', 'missing-preferences', 'old-manifest']) await t.test(kind, async child => {
    const f = await fixture(child); const java = await f.executable();
    if (kind === 'bootstrap') await writeFile(path.join(f.sourceRoot, 'bridge/bootstrap.js'), 'modified bootstrap');
    if (kind === 'bridge') await writeFile(path.join(f.stateDir, 'bridge/0.1.0/openpnp-codex-bridge.jar'), 'changed bridge');
    if (kind === 'runtime') await writeFile(path.join(f.openpnpHome, 'openpnp-gui.jar'), 'changed runtime');
    if (kind === 'ownership') { f.manifest.gui_ownership.api_version = 2; await f.repin(); }
    if (kind === 'extra-sample') await writeFile(path.join(f.openpnpHome, 'samples/unlisted.xml'), 'unlisted');
    if (kind === 'escape') { const outside = path.join(f.root, 'outside.jar'); await writeFile(outside, 'outside'); await symlink(outside, path.join(f.openpnpHome, 'lib/escape.jar')); f.manifest.files.push({ path: 'lib/escape.jar', sha256: hash('outside') }); await f.repin(); }
    if (kind === 'missing-preferences') { delete f.manifest.gui_launcher_jar; await f.repin(); }
    if (kind === 'old-manifest') { delete f.build.bootstrap_sha256; await f.repin(); }
    await assert.rejects(launchGuiSimulator({ ...f, java })); await absent(f.marker); await absent(path.join(f.stateDir, 'gui-simulators'));
  });
});

test('zero exit without manual bootstrap reports unattached, retains session, and does not replace an existing connection', async t => {
  const f = await fixture(t); await atomicJson(path.join(f.stateDir, 'connection.json'), { previous_connection: true });
  const result = await launchGuiSimulator({ ...f, java: await f.executable() });
  assert.equal(result.state, 'gui-exited-without-attachment'); assert.equal(result.connected, false); assert.equal(result.was_connected, false); assert.equal(result.evidence_preserved, true);
  assert.deepEqual(JSON.parse(await readFile(path.join(f.stateDir, 'connection.json'), 'utf8')), { previous_connection: true });
});

test('fragmented prefixed readiness requires authenticated GUI capability and never requests control', async t => {
  const f = await fixture(t); const methods = [];
  const port = await rpc(t, async (request, response) => {
    assert.equal(request.headers.authorization, `Bearer ${'A'.repeat(43)}`); let text = ''; for await (const chunk of request) text += chunk;
    methods.push(JSON.parse(text).method); response.setHeader('content-type','application/json'); response.end(JSON.stringify({ result: capability(f) }));
  });
  const result = await launchGuiSimulator({ ...f, java: await f.executable(readyBody(port)) }); assert.equal(result.was_connected, true); assert.equal(result.connected, false); assert.deepEqual(methods, ['openpnp_get_capabilities']);
  const connection = JSON.parse(await readFile(path.join(f.stateDir, 'connection.json'), 'utf8')); assert.equal(connection.machineId, 'installer-fixture-only'); assert.ok(connection.tokenFile.includes('/gui-simulators/')); assert.ok(!Object.hasOwn(connection, 'token'));
});

test('bad GUI attestation or auth preserves prior connection and fails without acquiring or retrying control', async t => {
  for (const kind of ['auth', 'hardware', 'profile', 'hash']) await t.test(kind, async child => {
    const f = await fixture(child); await atomicJson(path.join(f.stateDir, 'connection.json'), { previous_connection: true }); let requests = 0;
    const port = await rpc(child, (_request, response) => { requests++; if (kind === 'auth') { response.writeHead(403); response.end(); return; }
      response.end(JSON.stringify({ result: capability(f, kind === 'hardware' ? { hardware_qualified: true } : kind === 'profile' ? { simulator_profile: 'native-simulator' } : { bridge_artifact_sha256: '0'.repeat(64) }) }));
    });
    await assert.rejects(launchGuiSimulator({ ...f, java: await f.executable(readyBody(port, 5000)) }), /GUI (bridge connection|capability attestation)/); assert.equal(requests, 1);
    assert.deepEqual(JSON.parse(await readFile(path.join(f.stateDir, 'connection.json'), 'utf8')), { previous_connection: true });
  });
});

test('GUI exit while authentication is pending cannot create or report a verified connection', async t => {
  const f = await fixture(t); const port = await rpc(t, async (_request, response) => { await new Promise(resolve => setTimeout(resolve, 250)); response.end(JSON.stringify({ result: capability(f) })); });
  await assert.rejects(launchGuiSimulator({ ...f, java: await f.executable(readyBody(port, 50)) }), /exited before attachment verification/); await absent(path.join(f.stateDir, 'connection.json'));
});

test('GUI CLI handles its own forwarded stop, rejects unexpected child exit, and works through a symlink entrypoint', async t => {
  for (const intentional of [true, false]) await t.test(intentional ? 'intentional' : 'unexpected', async childTest => {
    const f = await fixture(childTest); const java = await f.executable("process.once('SIGTERM',()=>process.exit(143));setInterval(()=>{},1000);");
    const alias = path.join(f.root, 'cli-alias.mjs'); await symlink(cli, alias);
    const child = spawn(process.execPath, [alias, 'start-gui-simulator', '--state-dir', f.stateDir, '--openpnp-home', f.openpnpHome, '--java', java], { stdio: ['ignore','pipe','pipe'] });
    let stdout = '', stderr = '', fixturePid; const timer = setTimeout(() => { if (fixturePid) process.kill(fixturePid, 'SIGKILL'); child.kill('SIGKILL'); }, 10000);
    child.stderr.on('data', value => { stderr += value; }); child.stdout.on('data', value => { stdout += value; for (const line of stdout.split('\n')) { let receipt; try { receipt = JSON.parse(line); } catch { continue; } if (!fixturePid && receipt.state === 'waiting-for-local-bootstrap') { fixturePid = receipt.gui_pid; if (intentional) child.kill('SIGTERM'); else process.kill(fixturePid, 'SIGTERM'); } } });
    const code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve); }); clearTimeout(timer);
    assert.ok(fixturePid, stderr); assert.equal(code, intentional ? 0 : 1, stderr); assert.ok(!stdout.includes('"connected":true'));
    if (!intentional) assert.match(stderr, /exited unexpectedly/);
  });
});


test('explicit sensing profile prepares only a fresh fixture and binds its manifest without putting bridge on GUI classpath', async t => {
  const f = await fixture(t), java = await sensingExecutable(f);
  const prepared = await prepareGuiSimulator({ ...f, java, profile: 'vacuum-sensing', sensingScenario: 'invalid-read' });
  const launch = JSON.parse(await readFile(f.marker, 'utf8'));
  assert.equal(launch.stage, 'preparer'); assert.ok(launch.args.includes('org.openpnp.codex.GuiSensingFixture'));
  const moduleOpens = ['--add-opens=java.base/java.lang=ALL-UNNAMED', '--add-opens=java.desktop/java.awt=ALL-UNNAMED', '--add-opens=java.desktop/java.awt.color=ALL-UNNAMED'];
  assert.deepEqual(launch.args.filter(arg => arg.startsWith('--add-opens=')), moduleOpens, 'real native SimpleXML preparation needs the same Java module access as Main');
  assert.deepEqual(prepared.args.filter(arg => arg.startsWith('--add-opens=')), moduleOpens);
  assert.ok(launch.args[launch.args.indexOf('-cp') + 1].includes('/bridge/0.1.0/openpnp-codex-bridge.jar'));
  assert.ok(!prepared.args[prepared.args.indexOf('-cp') + 1].includes('/bridge/0.1.0/openpnp-codex-bridge.jar'));
  assert.equal(prepared.connected, false); assert.equal(prepared.local_grant, false);
  assert.equal(prepared.sensing_preparation.scenario, 'invalid-read'); assert.equal(prepared.sensing_preparation.source_installed_in_gui_process, false);
  assert.equal(property(prepared.args, 'openpnp.codex.sensingManifest'), prepared.sensing_preparation.manifest);
  assert.equal(property(prepared.args, 'openpnp.codex.sensingManifestSha256'), hash(await readFile(prepared.sensing_preparation.manifest)));
  assert.equal(property(prepared.args, 'openpnp.codex.sensingScenario'), 'invalid-read');
  await absent(path.join(f.stateDir, 'connection.json'));
});

test('invalid sensing selection rejects before executable and private session creation', async t => {
  for (const options of [{ profile: 'physical' }, { sensingScenario: 'success' }, { profile: 'vacuum-sensing', sensingScenario: 'arbitrary-readings' }]) {
    const f = await fixture(t), java = await sensingExecutable(f); await assert.rejects(prepareGuiSimulator({ ...f, java, ...options }), /profile|scenario/);
    await absent(f.marker); await absent(path.join(f.stateDir, 'gui-simulators'));
  }
});

test('failed sensing preparation preserves error receipt and cannot start GUI or overwrite a connection', async t => {
  const f = await fixture(t), java = await sensingExecutable(f, 'throw Error("GUI must not start");', true);
  await atomicJson(path.join(f.stateDir, 'connection.json'), { previous: true });
  await assert.rejects(prepareGuiSimulator({ ...f, java, profile: 'vacuum-sensing' }));
  const session = (await readdir(path.join(f.stateDir, 'gui-simulators')))[0];
  const failure = JSON.parse(await readFile(path.join(f.stateDir, 'gui-simulators', session, 'sensing-preparation-failure.json'), 'utf8'));
  assert.equal(failure.native_gui_started, false); assert.equal(failure.local_grant, false);
  assert.deepEqual(JSON.parse(await readFile(path.join(f.stateDir, 'connection.json'), 'utf8')), { previous: true });
});

test('sensing launch refuses a GUI attachment missing controlled-source attestation', async t => {
  const f = await fixture(t); let calls = 0;
  const port = await rpc(t, (_request, response) => { calls++; response.end(JSON.stringify({ result: capability(f) })); });
  const java = await sensingExecutable(f, readyBody(port, 5000));
  await assert.rejects(launchGuiSimulator({ ...f, java, profile: 'vacuum-sensing' }), /sensing capability attestation/);
  assert.equal(calls, 1); await absent(path.join(f.stateDir, 'connection.json'));
});


test('explicit sensing attestation binds a connected GUI without granting control', async t => {
  const f = await fixture(t); const methods = [];
  const port = await rpc(t, async (request, response) => {
    let body = ''; for await (const chunk of request) body += chunk; methods.push(JSON.parse(body).method);
    response.end(JSON.stringify({ result: capability(f, {
      gui_ownership: { local_grant: false, provenance: { sensing_fixture_attested: true } },
      vacuum_sensing: { available: true, source_profile: 'controlled-native-vacuum-v1', simulation_only: true, hardware_qualified: false }
    }) }));
  });
  const java = await sensingExecutable(f, readyBody(port, 500));
  const result = await launchGuiSimulator({ ...f, java, profile: 'vacuum-sensing', sensingScenario: 'retained-after-place' });
  assert.equal(result.was_connected, true); assert.equal(result.local_grant_requested_by_launcher, false);
  assert.deepEqual(methods, ['openpnp_get_capabilities']);
});
