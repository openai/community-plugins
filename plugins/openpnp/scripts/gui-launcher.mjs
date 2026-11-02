// SPDX-License-Identifier: Apache-2.0
// Verified native GUI simulator lifecycle. The explicit restart launcher supplies its separately validated original scope.
import { createHash, randomUUID } from 'node:crypto';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, mkdtemp, readFile, writeFile, open, rename, rm, lstat, realpath, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { javaEnvironment } from './java-environment.mjs';

const UPSTREAM = '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c';
const VERSION = '0.1.0';
const NATIVE_MODULE_OPENS = ['--add-opens=java.base/java.lang=ALL-UNNAMED', '--add-opens=java.desktop/java.awt=ALL-UNNAMED', '--add-opens=java.desktop/java.awt.color=ALL-UNNAMED'];
const pluginRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const validHash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
function inside(relative) { return typeof relative === 'string' && relative.length > 0 && !path.isAbsolute(relative) && !relative.includes('\\') && relative.split('/').every(part => part && part !== '.' && part !== '..'); }
async function readRegular(file, maxBytes, privateOnly = false) {
  const info = await lstat(file);
  if (!info.isFile() || info.isSymbolicLink() || info.size > maxBytes || (privateOnly && ((info.mode & 0o077) !== 0 || info.uid !== process.getuid()))) throw new Error('Expected a bounded regular file with the required private ownership.');
  return readFile(file);
}
async function privateDirectory(directory) {
  await mkdir(directory, { recursive: true, mode: 0o700 }); const info = await lstat(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || (info.mode & 0o077) !== 0 || info.uid !== process.getuid()) throw new Error('GUI state directories must be private directories owned by the current user, without symbolic links.');
}
async function writeJson(file, value) {
  const temporary = `${file}.${randomUUID()}.tmp`; let handle;
  try { handle = await open(temporary, 'wx', 0o600); await handle.writeFile(JSON.stringify(value, null, 2) + '\n'); await handle.sync(); await handle.close(); handle = undefined; await rename(temporary, file);
    const directory = await open(path.dirname(file), 'r'); try { await directory.sync(); } finally { await directory.close(); }
  } finally { await handle?.close(); await rm(temporary, { force: true }); }
}

/** Shared read-only runtime verification. No Java class is invoked here. */
export async function verifyGuiRuntime({ stateDir, openpnpHome, java = 'java', sourceRoot = pluginRoot }) {
  if (!['darwin', 'linux'].includes(process.platform)) throw new Error('This GUI simulator launcher requires the Unix private-file runtime; other platforms are not supported by this candidate.');
  if (!path.isAbsolute(stateDir || '') || !path.isAbsolute(openpnpHome || '')) throw new Error('Use absolute state and pinned OpenPnP runtime directories.');
  if (typeof java !== 'string' || !java || java.includes('\0')) throw new Error('Use a Java executable path or command name.');
  const receipt = JSON.parse((await readRegular(path.join(stateDir, 'installation.json'), 1024 * 1024, true)).toString());
  if (receipt.version !== VERSION || receipt.upstream_commit !== UPSTREAM || !path.isAbsolute(receipt.bridge_jar || '') || !validHash(receipt.bridge_sha256)) throw new Error('Installed bridge receipt is incompatible.');
  const bridgeJar = await realpath(receipt.bridge_jar), bridgeBytes = await readRegular(receipt.bridge_jar, 32 * 1024 * 1024, true);
  if (sha(bridgeBytes) !== receipt.bridge_sha256) throw new Error('Installed bridge integrity check failed.');
  const installed = JSON.parse((await readRegular(path.join(path.dirname(receipt.bridge_jar), 'build-manifest.json'), 1024 * 1024, true)).toString());
  if (installed.upstream_commit !== UPSTREAM || installed.bridge_version !== VERSION || installed.bridge_sha256 !== receipt.bridge_sha256 || !validHash(installed.bootstrap_sha256) || !validHash(installed.patched_native_jar_sha256) || !validHash(installed.runtime_manifest_sha256)) throw new Error('Install a GUI-enabled bridge build with explicit runtime and bootstrap hashes.');
  const bootstrapBytes = await readRegular(path.join(sourceRoot, 'bridge/bootstrap.js'), 65536);
  if (sha(bootstrapBytes) !== installed.bootstrap_sha256) throw new Error('Packaged GUI bootstrap integrity check failed.');
  const runtimeRoot = await realpath(openpnpHome), manifestPath = path.join(runtimeRoot, 'codex-build-manifest.json');
  const manifestBytes = await readRegular(manifestPath, 1024 * 1024), manifest = JSON.parse(manifestBytes.toString());
  if (sha(manifestBytes) !== installed.runtime_manifest_sha256 || manifest.upstream_commit !== UPSTREAM || manifest.gui_ownership?.api_version !== 1 || manifest.gui_ownership?.patch_id !== 'codex-gui-ownership-v1' || !validHash(manifest.gui_ownership?.patch_sha256) || manifest.native_action_observer?.api_version !== 1 || !validHash(manifest.native_action_observer?.patch_sha256)) throw new Error('GUI runtime/ownership/action-observer provenance is missing or incompatible.');
  if (!Array.isArray(manifest.files) || manifest.files.length < 1 || manifest.files.length > 10000) throw new Error('Invalid GUI runtime inventory.');
  const inventory = new Map();
  for (const entry of manifest.files) {
    if (!inside(entry.path) || !validHash(entry.sha256) || inventory.has(entry.path)) throw new Error('Invalid or duplicate GUI runtime manifest path.');
    const target = path.join(runtimeRoot, entry.path), resolved = await realpath(target);
    if (!resolved.startsWith(runtimeRoot + path.sep)) throw new Error('GUI runtime file escapes its distribution.');
    if (sha(await readRegular(target, 256 * 1024 * 1024)) !== entry.sha256) throw new Error('GUI runtime file integrity check failed.');
    inventory.set(entry.path, entry.sha256);
  }
  if (![manifest.gui_jar, manifest.gui_launcher_jar, manifest.libs_directory, manifest.samples_directory].every(inside) || !inventory.has(manifest.gui_jar) || !inventory.has(manifest.gui_launcher_jar) || inventory.get(manifest.gui_jar) !== installed.patched_native_jar_sha256) throw new Error('The patched native application and isolated-preferences JARs are not verified.');
  const jar = path.join(runtimeRoot, manifest.gui_jar), launcherJar = path.join(runtimeRoot, manifest.gui_launcher_jar), samples = path.join(runtimeRoot, manifest.samples_directory);
  const libraries = [...inventory.keys()].filter(file => file.startsWith(manifest.libs_directory + '/') && file.endsWith('.jar')).sort().map(file => path.join(runtimeRoot, file));
  if (!libraries.length || [launcherJar, jar, ...libraries].includes(bridgeJar) || [launcherJar, ...libraries].some(file => inventory.get(path.relative(runtimeRoot, file).split(path.sep).join('/')) === receipt.bridge_sha256)) throw new Error('GUI classpath must contain verified native libraries with the bridge excluded.');
  async function checkSamples(directory) {
    const info = await lstat(directory); if (!info.isDirectory() || info.isSymbolicLink()) throw new Error('GUI samples must remain inside a regular verified directory.');
    for (const entry of await readdir(directory, { withFileTypes: true })) { const file = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) throw new Error('GUI samples cannot contain symbolic links.');
      if (entry.isDirectory()) await checkSamples(file); else if (!entry.isFile() || !inventory.has(path.relative(runtimeRoot, file).split(path.sep).join('/'))) throw new Error('GUI sample asset is outside the verified inventory.');
    }
  }
  await checkSamples(samples);
  return { receipt, installed, manifest, manifestPath, runtimeRoot, bridgeJar, bootstrapBytes, jar, launcherJar, samples, libraries, inventory };
}

/** Verify every runtime input before creating a fresh session or invoking any executable. */
export async function prepareGuiSimulator({ stateDir, openpnpHome, java = 'java', sourceRoot = pluginRoot, profile = 'gui-simulator', sensingScenario }) {
  if (!['gui-simulator', 'vacuum-sensing'].includes(profile)) throw new Error('GUI profile must be gui-simulator or vacuum-sensing.');
  if (sensingScenario !== undefined && (profile !== 'vacuum-sensing' || !['success', 'missed-pick-retry', 'retained-after-place', 'lost-before-place', 'invalid-read'].includes(sensingScenario))) throw new Error('GUI sensing scenario requires vacuum-sensing and one declared fixed scenario.');
  const { receipt, installed, manifest, manifestPath, bridgeJar, bootstrapBytes, jar, launcherJar, samples, libraries, inventory } = await verifyGuiRuntime({ stateDir, openpnpHome, java, sourceRoot });
  await privateDirectory(stateDir); const sessions = path.join(stateDir, 'gui-simulators'); await privateDirectory(sessions);
  const sessionRoot = await mkdtemp(path.join(sessions, 'gui-'));
  const config = path.join(sessionRoot, 'config'), preferences = path.join(sessionRoot, 'preferences'), nativeState = path.join(sessionRoot, 'bridge-state'), home = path.join(sessionRoot, 'home');
  for (const directory of [config, path.join(config, 'scripts'), preferences, path.join(preferences, 'user'), path.join(preferences, 'system'), nativeState, home]) await privateDirectory(directory);
  const { environment, ignored: ignoredJavaEnvironment } = javaEnvironment();
  let sensingPreparation;
  if (profile === 'vacuum-sensing') {
    const scenario = sensingScenario ?? 'success', sensingManifest = path.join(sessionRoot, 'sensing-fixture.json');
    const prepareArgs = ['-Xmx2g', '-XX:+ExitOnOutOfMemoryError', '-Djava.awt.headless=true', ...NATIVE_MODULE_OPENS, `-Duser.home=${home}`, '-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory',
      '-cp', [bridgeJar, launcherJar, jar, ...libraries].join(path.delimiter), 'org.openpnp.codex.GuiSensingFixture', '--config-dir', config, '--manifest', sensingManifest, '--scenario', scenario];
    let prepared;
    try { prepared = await promisify(execFile)(java, prepareArgs, { cwd: sessionRoot, env: environment, timeout: 60000, maxBuffer: 1024 * 1024, windowsHide: true }); }
    catch (error) { await writeJson(path.join(sessionRoot, 'sensing-preparation-failure.json'), { state: 'fixture-preparation-failed', profile, scenario, message: String(error.message).slice(0, 4000), native_gui_started: false, local_grant: false }); throw error; }
    await writeFile(path.join(sessionRoot, 'sensing-preparation.log'), prepared.stdout + prepared.stderr, { flag: 'wx', mode: 0o600 });
    const manifestBytes = await readRegular(sensingManifest, 1024 * 1024, true);
    sensingPreparation = { profile, scenario, manifest: sensingManifest, sha256: sha(manifestBytes), local_grant: false, source_installed_in_gui_process: false };
  }
  await privateDirectory(path.join(config, 'scripts'));
  const bootstrap = path.join(config, 'scripts', 'codex-bootstrap.js'); await writeFile(bootstrap, bootstrapBytes, { flag: 'wx', mode: 0o600 });
  const properties = { configDir: config, 'java.util.prefs.PreferencesFactory': 'org.openpnp.codex.IsolatedPreferencesFactory', 'java.util.prefs.userRoot': path.join(preferences, 'user'), 'java.util.prefs.systemRoot': path.join(preferences, 'system'), 'user.home': home,
    'openpnp.codex.bridgeJar': bridgeJar, 'openpnp.codex.bridgeSha256': receipt.bridge_sha256, 'openpnp.codex.stateDir': nativeState, 'openpnp.codex.sampleRoot': samples,
    'openpnp.codex.bootstrapPath': bootstrap, 'openpnp.codex.bootstrapSha256': installed.bootstrap_sha256, 'openpnp.codex.runtimeManifest': manifestPath, 'openpnp.codex.runtimeManifestSha256': installed.runtime_manifest_sha256 };
  if (sensingPreparation) Object.assign(properties, { 'openpnp.codex.sensingManifest': sensingPreparation.manifest, 'openpnp.codex.sensingManifestSha256': sensingPreparation.sha256, 'openpnp.codex.sensingScenario': sensingPreparation.scenario });
  const args = ['-Xmx2g', '-XX:+ExitOnOutOfMemoryError', '-Djava.awt.headless=false', ...NATIVE_MODULE_OPENS,
    ...(process.platform === 'darwin' ? ['--add-exports=java.desktop/com.apple.eawt=ALL-UNNAMED'] : []), ...Object.entries(properties).map(([key, value]) => `-D${key}=${value}`), '-cp', [launcherJar, jar, ...libraries].join(path.delimiter), 'org.openpnp.Main'];
  const prepared = { gui_session_id: path.basename(sessionRoot), session_root: sessionRoot, config_directory: config, preferences_directory: preferences, native_state_directory: nativeState, bootstrap_script: bootstrap, java, args,
    bridge_sha256: receipt.bridge_sha256, runtime_manifest_sha256: installed.runtime_manifest_sha256, bootstrap_sha256: installed.bootstrap_sha256, bridge_on_application_classpath: false, preferences_scope: 'fresh-session-in-memory; not persisted', launcher_preferences_jar_sha256: inventory.get(manifest.gui_launcher_jar),
    profile, ...(sensingPreparation ? { sensing_preparation: sensingPreparation } : {}), ignored_java_environment: ignoredJavaEnvironment, examples_policy: 'Native attachment preserves only exact resource-matching generated examples outside active scripts; changed/unknown scripts remain untouched and block attachment.', connected: false, local_grant: false, state: 'prepared', physical_qualification: false };
  await writeJson(path.join(sessionRoot, 'launcher.json'), prepared);
  return { ...prepared, environment, stateDir };
}

async function verifyAttachment(prepared, port, isAlive) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid GUI readiness port.');
  const tokenFile = path.join(prepared.native_state_directory, 'bridge.token'); const token = (await readRegular(tokenFile, 4096, true)).toString().trim();
  if (!/^[A-Za-z0-9_-]{32,256}$/.test(token)) throw new Error('GUI native token format is invalid.');
  const url = `http://127.0.0.1:${port}/`;
  const response = await fetch(new URL('/rpc', url), { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(10000), headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify({ method: 'openpnp_get_capabilities', params: {} }) });
  if (!response.ok) throw new Error(`GUI bridge connection failed (HTTP ${response.status}).`);
  const chunks = []; let length = 0;
  for await (const chunk of response.body) { length += chunk.length; if (length > 1024 * 1024) throw new Error('GUI capability response exceeds its bound.'); chunks.push(Buffer.from(chunk)); }
  const bridge = JSON.parse(Buffer.concat(chunks).toString('utf8')).result;
  if (bridge?.upstream_commit !== UPSTREAM || bridge?.schema_version !== 1 || bridge?.bridge_version !== VERSION || bridge?.simulator_profile !== 'gui-simulator' || bridge?.simulation !== true || bridge?.hardware_qualified !== false || bridge?.bridge_artifact_sha256 !== prepared.bridge_sha256 || typeof bridge?.machine_id !== 'string' || !bridge.machine_id) throw new Error('GUI capability attestation does not match the prepared bridge and simulator.');
  if (!prepared.sensing_restart && prepared.sensing_preparation && (bridge.gui_ownership?.provenance?.sensing_fixture_attested !== true || bridge.vacuum_sensing?.available !== true || bridge.vacuum_sensing?.source_profile !== 'controlled-native-vacuum-v1' || bridge.vacuum_sensing?.simulation_only !== true || bridge.vacuum_sensing?.hardware_qualified !== false)) throw new Error('GUI sensing capability attestation does not match the deliberately prepared controlled simulator.');
  if (prepared.sensing_restart) {
    const provenance = bridge.gui_ownership?.provenance, restart = provenance?.sensing_fixture;
    if (bridge.machine_id !== prepared.sensing_restart.machine_id || provenance?.sensing_restart_attested !== true || provenance?.sensing_fixture_attested !== false || restart?.profile !== 'native-gui-source-absent-restart-v1' || restart?.source_authority_created !== false || restart?.execution_authority_restored !== false || restart?.simulation_only !== true || restart?.hardware_qualified !== false || bridge.vacuum_sensing?.available !== false || bridge.sensing_reconciliation?.restart_request_available !== true)
      throw new Error('GUI restart attachment must attest the original machine and absent source without restored authority.');
  }
  const connection = { schemaVersion: 1, url, tokenFile, machineId: bridge.machine_id };
  if (!isAlive()) throw new Error('GUI exited before attachment verification completed.');
  const file = path.join(prepared.stateDir, 'connection.json'); await writeJson(file, connection);
  return { connected: true, connection_file: file, machine_id: bridge.machine_id, simulator_profile: 'gui-simulator', local_grant: typeof bridge.gui_ownership?.local_grant === 'boolean' ? bridge.gui_ownership.local_grant : null, local_grant_requested_by_launcher: false,
    control_state: 'Local control grant is observed separately; the launcher never acquires a lease or grants control.', gui_qualification: bridge.gui_ownership?.gui_qualification ?? 'unknown', physical_qualification: false };
}

/** Foreground lifecycle; waiting for a manual bootstrap is not a connected bridge. */
export async function launchGuiSimulator(options) {
  return launchPreparedGuiSimulator(await prepareGuiSimulator(options));
}

/** Accept only a prepared internal launcher record, never raw CLI JVM arguments. */
export async function launchPreparedGuiSimulator(prepared) {
  await prepared.beforeLaunch?.();
  const child = spawn(prepared.java, prepared.args, { cwd: prepared.session_root, env: prepared.environment, stdio: ['ignore', 'pipe', 'pipe'], shell: false });
  let connected = false, attached = false, intentionalStop = false, verification, startupError;
  const pending = { stdout: '', stderr: '' };
  const publish = value => process.stdout.write(JSON.stringify(value) + '\n');
  const initial = { gui_session_id: prepared.gui_session_id, gui_pid: child.pid, session_root: prepared.session_root, config_directory: prepared.config_directory, bootstrap_script: prepared.bootstrap_script, connected: false, local_grant: false, state: 'waiting-for-local-bootstrap',
    next_steps: ['Complete the native Welcome dialog.', 'Run Scripts → codex-bootstrap.js.', 'In the visible local controller, choose Allow Codex control when ready.'], lifecycle: 'Foreground launcher. Closing the GUI or Ctrl-C ends this session; configuration, logs and journal are preserved.', physical_qualification: false };
  let childReceipt;
  child.once('spawn', () => { childReceipt = writeJson(path.join(prepared.session_root, 'launcher-process.json'), { schema_version: 1, gui_pid: child.pid, launcher_pid: process.pid, config_directory: prepared.config_directory, native_state_directory: prepared.native_state_directory, started_at: new Date().toISOString() }).catch(error => { startupError = error; terminate(); }); publish(initial); });
  function inspect(stream, chunk) {
    pending[stream] += chunk.toString('utf8'); if (pending[stream].length > 1024 * 1024) pending[stream] = pending[stream].slice(-65536);
    let newline;
    while ((newline = pending[stream].indexOf('\n')) !== -1) {
      const line = pending[stream].slice(0, newline); pending[stream] = pending[stream].slice(newline + 1); process.stderr.write(line + '\n');
      const match = /(?:^|\s)OPENPNP_CODEX_GUI_READY port=(\d+) mode=gui-simulator\s*$/.exec(line);
      if (match && !attached) { attached = true; verification = verifyAttachment(prepared, Number(match[1]), () => child.exitCode === null && child.signalCode === null).then(result => { if (child.exitCode !== null || child.signalCode !== null) throw new Error('GUI exited before its verified connection could be reported.'); connected = true; publish({ ...result, gui_session_id: prepared.gui_session_id, gui_pid: child.pid }); }).catch(error => { startupError = error; terminate(); }); }
    }
  }
  child.stdout.on('data', chunk => inspect('stdout', chunk)); child.stderr.on('data', chunk => inspect('stderr', chunk));
  let killTimer;
  function terminate() { if (child.exitCode !== null || child.signalCode !== null) return; child.kill('SIGTERM'); killTimer ??= setTimeout(() => { if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL'); }, 5000); killTimer.unref(); }
  const stop = () => { intentionalStop = true; terminate(); }; process.once('SIGINT', stop); process.once('SIGTERM', stop);
  let code, signal;
  try {
    await new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', (exitCode, exitSignal) => { code = exitCode; signal = exitSignal; resolve(); }); });
    await childReceipt;
    await verification;
    if (startupError) throw startupError;
    const requestedStop = intentionalStop && (code === 143 || code === 130 || signal === 'SIGTERM' || signal === 'SIGINT' || signal === 'SIGKILL');
    if (code !== 0 && !requestedStop) throw new Error(`GUI simulator exited unexpectedly (${signal || `code ${code}`}); preserved session ${prepared.session_root}.`);
    const final = { gui_session_id: prepared.gui_session_id, session_root: prepared.session_root, connected: false, was_connected: connected, state: connected ? 'gui-session-ended' : 'gui-exited-without-attachment', local_grant_requested_by_launcher: false, evidence_preserved: true, intentional_stop: intentionalStop, forced_stop: signal === 'SIGKILL' };
    await writeJson(path.join(prepared.session_root, 'launcher-result.json'), final); return final;
  } catch (error) {
    try { await writeJson(path.join(prepared.session_root, 'launcher-result.json'), { gui_session_id: prepared.gui_session_id, connected: false, was_connected: connected, state: 'launcher-failed', error: error.message, evidence_preserved: true, local_grant_requested_by_launcher: false }); } catch { /* Preserve the original launch error. */ }
    throw error;
  } finally { clearTimeout(killTimer); process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop); }
}
