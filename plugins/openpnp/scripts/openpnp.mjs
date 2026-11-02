#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { realpathSync, constants } from 'node:fs';
import { mkdir, readFile, writeFile, open, rename, link, lstat, realpath, readdir, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { inspectJournal } from './diagnostics.mjs';
import { launchGuiSimulator } from './gui-launcher.mjs';
import { launchRestartGuiSimulator } from './gui-restart-launcher.mjs';
import { launchControllerSimulator } from './controller-launcher.mjs';
import { javaEnvironment } from './java-environment.mjs';

const pluginRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const UPSTREAM = '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c';
const VERSION = '0.1.0';
export function stateDirectory() {
  return process.env.OPENPNP_STATE_DIR || path.join(process.platform === 'darwin' ? path.join(os.homedir(), 'Library', 'Application Support') :
    process.platform === 'win32' ? (process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')) : (process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share')), 'openpnp-codex');
}
function sha256(data) { return createHash('sha256').update(data).digest('hex'); }
export async function readBoundedRegular(file, limit) {
  const handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  try {
    const info = await handle.stat();
    if (!info.isFile() || info.size > limit) throw new Error('Expected a bounded regular file.');
    const chunks = []; let total = 0;
    while (true) {
      const buffer = Buffer.alloc(Math.min(65536, limit + 1 - total));
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
      if (!bytesRead) break;
      total += bytesRead;
      if (total > limit) throw new Error('File grew beyond its bounded read limit.');
      chunks.push(buffer.subarray(0, bytesRead));
    }
    return Buffer.concat(chunks, total);
  } finally { await handle.close(); }
}
function separateTrees(left, right, description) {
  if (left === right || left.startsWith(right + path.sep) || right.startsWith(left + path.sep)) throw new Error(`${description} must use separate directory trees.`);
}
export function parseArguments(args) {
  const [command = 'help', ...rest] = args;
  const options = {};
  for (let i = 0; i < rest.length; i += 2) {
    const key = rest[i]; const value = rest[i + 1];
    if (!/^--[a-z][a-z0-9-]*$/.test(key) || !value || value.startsWith('--') || key in options) throw new Error('Use unique --option value pairs.');
    options[key] = value;
  }
  const allowed = {
    help: [], 'install-bridge': ['--state-dir'],
    configure: ['--state-dir', '--url', '--token-file'],
    doctor: ['--state-dir', '--connection-file'],
    diagnostics: ['--state-dir', '--output'],
    'support-export': ['--state-dir', '--output', '--operation-ids', '--artifacts'],
    'inspect-controller-history': ['--state-dir', '--openpnp-home', '--java', '--journal'],
    'start-controller-simulator': ['--state-dir', '--openpnp-home', '--java'],
    'start-simulator': ['--state-dir', '--openpnp-home', '--java', '--port', '--profile', '--sensing-scenario'],
    'restart-gui-simulator': ['--state-dir', '--openpnp-home', '--java', '--session-dir'],
    'start-gui-simulator': ['--state-dir', '--openpnp-home', '--java', '--profile', '--sensing-scenario'],
    'adopt-configuration': ['--state-dir', '--openpnp-home', '--java', '--bundle', '--sha256', '--destination'],
    'start-adopted-simulator': ['--state-dir', '--openpnp-home', '--java', '--adoption-dir', '--port'],
    'uninstall-bridge': ['--state-dir', '--version'],
  };
  if (!(command in allowed) || Object.keys(options).some(key => !allowed[command].includes(key))) throw new Error('Unknown command or option. Run help for supported commands.');
  return { command, options };
}
export function supportSelections(options) {
  const operations = options['--operation-ids']; const artifacts = options['--artifacts'];
  if (operations !== undefined && (typeof operations !== 'string' || operations.length > 4096 || !operations.length))
    throw new Error('Use a bounded comma-separated list of operation UUIDs.');
  if (artifacts !== undefined && (typeof artifacts !== 'string' || artifacts.length > 4096 || !artifacts.length))
    throw new Error('Use a bounded list of UUID:SHA256:camera or UUID:SHA256:job-document selections.');
  return {
    operationIds: operations === undefined ? [] : operations.split(','),
    artifactSelections: artifacts === undefined ? [] : artifacts.split(',').map(item => {
      const fields = item.split(':');
      if (fields.length !== 3) throw new Error('Each artifact selection must be UUID:SHA256:camera or UUID:SHA256:job-document.');
      return { artifact_id: fields[0], sha256: fields[1], kind: fields[2] };
    }),
  };
}
export async function atomicJson(file, value) {
  await mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${randomUUID()}.tmp`;
  const handle = await open(temporary, 'wx', 0o600);
  try { await handle.writeFile(JSON.stringify(value, null, 2) + '\n'); await handle.sync(); } finally { await handle.close(); }
  try {
    await rename(temporary, file);
    if (process.platform !== 'win32') { const dir = await open(path.dirname(file), 'r'); try { await dir.sync(); } finally { await dir.close(); } }
  } finally { await rm(temporary, { force: true }); }
}
export function endpoint(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(url.hostname) || !url.port || url.pathname !== '/' || url.username || url.password || url.search || url.hash) throw new Error('Bridge URL must be an HTTP loopback IP with an explicit port.');
  return url;
}
async function privateToken(file) {
  if (!path.isAbsolute(file)) throw new Error('Token file path must be absolute.');
  const info = await lstat(file);
  if (!info.isFile() || info.isSymbolicLink() || info.size > 4096 || (process.platform !== 'win32' && ((info.mode & 0o077) || info.uid !== process.getuid()))) throw new Error('Token file must be a regular file owned by you with mode 0600.');
  const token = (await readFile(file, 'utf8')).trim();
  if (!/^[A-Za-z0-9_-]{32,256}$/.test(token)) throw new Error('Token format is invalid.');
  return token;
}
async function readConnection(file) {
  const info = await lstat(file);
  if (!info.isFile() || info.isSymbolicLink() || info.size > 16384 || (process.platform !== 'win32' && ((info.mode & 0o077) || info.uid !== process.getuid()))) throw new Error('Connection file must be a regular file owned by you with mode 0600.');
  const value = JSON.parse(await readFile(file, 'utf8'));
  endpoint(value.url);
  return value;
}
async function capabilities(url, tokenFile) {
  const token = await privateToken(tokenFile);
  const response = await fetch(new URL('/rpc', endpoint(url)), { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(10000),
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify({ method: 'openpnp_get_capabilities', params: {} }) });
  if (!response.ok) throw new Error(`Bridge connection failed (HTTP ${response.status}).`);
  const data = await response.json();
  if (!data.result || data.result.upstream_commit !== UPSTREAM || data.result.schema_version !== 1) throw new Error('Bridge build or protocol does not match this plugin.');
  return data.result;
}
export async function configure({ stateDir, url, tokenFile }) {
  endpoint(url);
  const bridge = await capabilities(url, tokenFile);
  const file = path.join(stateDir, 'connection.json');
  await atomicJson(file, { schemaVersion: 1, url: endpoint(url).toString(), tokenFile, machineId: bridge.machine_id });
  return { connection_file: file, connected: true, upstream_commit: bridge.upstream_commit, simulation: bridge.simulation, hardware_qualified: bridge.hardware_qualified };
}
export async function installBridge(stateDir, sourceRoot = pluginRoot) {
  const manifestPath = path.join(sourceRoot, 'bridge', 'build-manifest.json');
  let manifest;
  try { manifest = JSON.parse(await readFile(manifestPath, 'utf8')); } catch { throw new Error('Native bridge is not built. Run the repository native build before installation.'); }
  if (manifest.upstream_commit !== UPSTREAM || manifest.bridge_version !== VERSION || !/^[a-f0-9]{64}$/.test(manifest.bridge_sha256)) throw new Error('Native bridge build manifest is incompatible.');
  const source = path.join(sourceRoot, 'bridge', 'openpnp-codex-bridge.jar');
  const data = await readFile(source);
  if (sha256(data) !== manifest.bridge_sha256) throw new Error('Native bridge integrity check failed.');
  const destination = path.join(stateDir, 'bridge', VERSION);
  await mkdir(destination, { recursive: true, mode: 0o700 });
  const target = path.join(destination, 'openpnp-codex-bridge.jar');
  try {
    const existing = await readFile(target);
    if (sha256(existing) !== manifest.bridge_sha256) throw new Error('This installed version has different content; use a new version instead of overwriting it.');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    const staging = path.join(stateDir, 'staging'); await mkdir(staging, { recursive: true, mode: 0o700 });
    const temporary = path.join(staging, `${randomUUID()}.bridge.tmp`);
    const handle = await open(temporary, 'wx', 0o600);
    try {
      try { await handle.writeFile(data); await handle.sync(); } finally { await handle.close(); }
      try { await link(temporary, target); }
      catch (publicationError) {
        if (publicationError.code !== 'EEXIST' || sha256(await readFile(target)) !== manifest.bridge_sha256) throw publicationError;
      }
      if (process.platform !== 'win32') { const dir = await open(destination, 'r'); try { await dir.sync(); } finally { await dir.close(); } }
    } finally { await rm(temporary, { force: true }); }
  }
  await atomicJson(path.join(destination, 'build-manifest.json'), manifest);
  await atomicJson(path.join(stateDir, 'installation.json'), { schema_version: 1, version: VERSION, bridge_jar: target, upstream_commit: UPSTREAM, bridge_sha256: manifest.bridge_sha256 });
  return { installed: true, version: VERSION, bridge_jar: target, journals_preserved: true, connection_created: false };
}
export async function doctor(stateDir, connectionFile = path.join(stateDir, 'connection.json')) {
  const checks = [];
  const [major, minor] = process.versions.node.split('.').map(Number);
  checks.push({ check: 'node', ok: major > 22 || (major === 22 && minor >= 19), version: process.versions.node });
  try {
    const connection = await readConnection(connectionFile);
    const bridge = await capabilities(connection.url, connection.tokenFile);
    checks.push({ check: 'bridge', ok: true, capabilities: bridge });
  } catch (error) { checks.push({ check: 'bridge', ok: false, message: error.message }); }
  try {
    const receipt = JSON.parse(await readFile(path.join(stateDir, 'installation.json'), 'utf8'));
    const hash = sha256(await readFile(receipt.bridge_jar));
    checks.push({ check: 'installed_bridge_integrity', ok: hash === receipt.bridge_sha256, version: receipt.version });
  } catch { checks.push({ check: 'installed_bridge_integrity', ok: false, message: 'No valid local installation receipt.' }); }
  return { ok: checks.every(check => check.ok), checks, hardware_qualification: 'unavailable' };
}
export async function verifiedRuntime({ stateDir, openpnpHome, sourceRoot }) {
  if (!openpnpHome || !path.isAbsolute(openpnpHome)) throw new Error('--openpnp-home must be an absolute path to the pinned OpenPnP build.');
  const receipt = sourceRoot ? { version: VERSION, upstream_commit: UPSTREAM, bridge_jar: path.join(sourceRoot, 'bridge/openpnp-codex-bridge.jar') } :
    JSON.parse((await readBoundedRegular(path.join(stateDir, 'installation.json'), 1024 * 1024)).toString('utf8'));
  if (receipt.upstream_commit !== UPSTREAM || receipt.version !== VERSION || !path.isAbsolute(receipt.bridge_jar || '')) throw new Error('Installed bridge receipt is incompatible.');
  const installed = JSON.parse((await readBoundedRegular(path.join(path.dirname(receipt.bridge_jar), 'build-manifest.json'), 1024 * 1024)).toString('utf8'));
  if (sourceRoot) receipt.bridge_sha256 = installed.bridge_sha256;
  if (installed.upstream_commit !== UPSTREAM || installed.bridge_version !== VERSION || installed.bridge_sha256 !== receipt.bridge_sha256 ||
      !/^[a-f0-9]{64}$/.test(installed.runtime_manifest_sha256 || '') || !/^[a-f0-9]{64}$/.test(installed.patched_native_jar_sha256 || '') ||
      sha256(await readBoundedRegular(receipt.bridge_jar, 32 * 1024 * 1024)) !== receipt.bridge_sha256) throw new Error('Installed bridge integrity or runtime binding is incompatible.');
  const manifestBytes = await readBoundedRegular(path.join(openpnpHome, 'codex-build-manifest.json'), 1024 * 1024);
  if (sha256(manifestBytes) !== installed.runtime_manifest_sha256) throw new Error('OpenPnP runtime manifest does not match the installed bridge build.');
  const nativeManifest = JSON.parse(manifestBytes.toString('utf8'));
  if (nativeManifest.upstream_commit !== UPSTREAM || !Array.isArray(nativeManifest.files) || nativeManifest.files.length === 0) throw new Error('OpenPnP build provenance is missing or incompatible. Use the repository native build.');
  const nativeRoot = await realpath(openpnpHome);
  if (stateDir) separateTrees(await realpath(stateDir), nativeRoot, 'Operational state and OpenPnP runtime');
  const verifiedFiles = new Set();
  for (const entry of nativeManifest.files) {
    if (typeof entry.path !== 'string' || path.isAbsolute(entry.path) || entry.path.split(/[\\/]/).some(part => part === '..') || !/^[a-f0-9]{64}$/.test(entry.sha256)) throw new Error('Invalid OpenPnP file manifest.');
    if (verifiedFiles.has(entry.path)) throw new Error('Duplicate OpenPnP manifest file.');
    const target = await realpath(path.join(openpnpHome, entry.path));
    if (!target.startsWith(nativeRoot + path.sep) || !(await lstat(target)).isFile()) throw new Error('OpenPnP file escapes its distribution.');
    if (sha256(await readFile(path.join(openpnpHome, entry.path))) !== entry.sha256) throw new Error('OpenPnP distribution integrity check failed.');
    verifiedFiles.add(entry.path);
  }
  for (const relative of [nativeManifest.gui_jar, nativeManifest.samples_directory, nativeManifest.libs_directory]) {
    if (typeof relative !== 'string' || path.isAbsolute(relative) || relative.split(/[\\/]/).some(part => part === '..')) throw new Error('OpenPnP runtime paths must stay inside the verified distribution.');
  }
  const jar = path.join(openpnpHome, nativeManifest.gui_jar);
  const samples = path.join(openpnpHome, nativeManifest.samples_directory);
  if (!verifiedFiles.has(nativeManifest.gui_jar)) throw new Error('The OpenPnP application JAR is not in the verified manifest.');
  if (sha256(await readFile(jar)) !== installed.patched_native_jar_sha256) throw new Error('OpenPnP application JAR does not match the installed bridge build.');
  const libraries = [...verifiedFiles].filter(file => file.startsWith(nativeManifest.libs_directory + '/') && file.endsWith('.jar')).sort().map(file => path.join(openpnpHome, file));
  if (!libraries.length) throw new Error('No verified OpenPnP libraries found.');
  if ([receipt.bridge_jar, jar, ...libraries].some(file => file.includes(path.delimiter))) throw new Error('Java classpath filenames cannot contain the classpath separator.');
  async function verifySampleInventory(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) throw new Error('Sample assets must not contain symbolic links.');
      if (entry.isDirectory()) await verifySampleInventory(absolute);
      else if (!entry.isFile() || !verifiedFiles.has(path.relative(openpnpHome, absolute).split(path.sep).join('/'))) throw new Error('Sample asset is outside the verified inventory.');
    }
  }
  await verifySampleInventory(samples);
  return { receipt, jar, libraries, samples, nativeRoot };
}
export async function launchSimulator({ stateDir, openpnpHome, java = 'java', port = '0', profile = 'native-simulator', adoptionDir, sensingScenario }) {
  if (!openpnpHome || !path.isAbsolute(openpnpHome)) throw new Error('--openpnp-home must be an absolute path to the pinned OpenPnP build.');
  if (!/^\d+$/.test(port) || Number(port) > 65535) throw new Error('--port must be 0–65535.');
  if (!(adoptionDir ? profile === 'adopted-simulator' : ['native-simulator', 'sustained-workload', 'vacuum-sensing'].includes(profile))) throw new Error('--profile must be native-simulator, sustained-workload or vacuum-sensing.');
  if (sensingScenario !== undefined && (profile !== 'vacuum-sensing' || !['success', 'missed-pick-retry', 'retained-after-place', 'lost-before-place', 'invalid-read'].includes(sensingScenario))) {
    throw new Error('--sensing-scenario requires the vacuum-sensing profile and one fixed scenario: success, missed-pick-retry, retained-after-place, lost-before-place or invalid-read.');
  }
  const { receipt, jar, libraries, samples } = await verifiedRuntime({ stateDir, openpnpHome });
  await mkdir(stateDir, { recursive: true, mode: 0o700 });
  const tokenFile = path.join(stateDir, 'bridge.token');
  try { await privateToken(tokenFile); } catch (error) {
    try { await lstat(tokenFile); } catch (missing) { if (missing.code === 'ENOENT') { await writeFile(tokenFile, randomBytes(32).toString('base64url') + '\n', { flag: 'wx', mode: 0o600 }); } else throw missing; }
    await privateToken(tokenFile);
  }
  const args = ['-Xmx2g', '-XX:+ExitOnOutOfMemoryError', '-Dfile.encoding=UTF-8', '-Djava.awt.headless=true', '--add-opens=java.base/java.lang=ALL-UNNAMED', '--add-opens=java.desktop/java.awt=ALL-UNNAMED', '--add-opens=java.desktop/java.awt.color=ALL-UNNAMED',
    '-cp', [receipt.bridge_jar, jar, ...libraries].join(path.delimiter), adoptionDir ? 'org.openpnp.codex.NativeAdoptedSimulatorMain' : 'org.openpnp.codex.SimulatorMain',
    '--token-file', tokenFile, '--journal-dir', path.join(stateDir, 'journal'), '--sample-root', samples, '--port', port,
    ...(adoptionDir ? ['--adoption-dir', adoptionDir] : ['--config-dir', path.join(stateDir, 'simulator-configs'), '--profile', profile]),
    ...(profile === 'vacuum-sensing' ? ['--sensing-scenario', sensingScenario ?? 'success'] : [])];
  const child = spawn(java, args, { env: javaEnvironment().environment, stdio: ['ignore', 'pipe', 'inherit'], shell: false });
  let pending = ''; let configured = false; let configurePromise; let startupError; let killTimer;
  const terminate = () => { child.kill('SIGTERM'); killTimer ||= setTimeout(() => child.kill('SIGKILL'), 5000); };
  const startupTimer = setTimeout(() => { startupError = new Error('Native simulator did not become ready within 60 seconds.'); terminate(); }, 60000);
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', chunk => {
    pending += chunk.toString('utf8');
    if (pending.length > 1024 * 1024) pending = pending.slice(-65536);
    let newline;
    while ((newline = pending.indexOf('\n')) !== -1) {
      const line = pending.slice(0, newline); pending = pending.slice(newline + 1);
      const match = /^OPENPNP_CODEX_READY port=(\d+) upstream=([a-f0-9]+) mode=(native-simulator|sustained-workload|vacuum-sensing|adopted-simulator)$/.exec(line.trim());
      if (match && match[2] === UPSTREAM && match[3] === profile && !configured) {
        configured = true;
        configurePromise = configure({ stateDir, url: `http://127.0.0.1:${match[1]}/`, tokenFile }).then(result => {
          clearTimeout(startupTimer);
          process.stdout.write(JSON.stringify({ ...result, simulator_pid: child.pid, lifecycle: 'foreground; Ctrl-C stops the simulator' }) + '\n');
        }).catch(error => { startupError = error; terminate(); });
      } else process.stderr.write(line + '\n');
    }
  });
  let intentionalStop = false;
  const stop = () => { intentionalStop = true; terminate(); };
  process.once('SIGINT', stop); process.once('SIGTERM', stop);
  try {
    await new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('exit', (code, signal) => {
        const requestedStop = intentionalStop && (code === 143 || code === 130 || signal === 'SIGTERM' || signal === 'SIGINT');
        if (code === 0 || requestedStop) resolve();
        else reject(new Error(`Simulator exited unexpectedly (${signal || `code ${code}`}).`));
      });
    });
    await configurePromise;
    if (startupError) throw startupError;
    if (!configured) throw new Error('Native simulator exited before readiness was verified.');
  } finally { clearTimeout(startupTimer); clearTimeout(killTimer); process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop); }
}

function nativeArguments(runtime, mainClass, options) {
  return ['-Xmx2g', '-XX:+ExitOnOutOfMemoryError', '-Dfile.encoding=UTF-8', '-Djava.awt.headless=true',
    '--add-opens=java.base/java.lang=ALL-UNNAMED', '--add-opens=java.desktop/java.awt=ALL-UNNAMED',
    '--add-opens=java.desktop/java.awt.color=ALL-UNNAMED', '-cp',
    [runtime.receipt.bridge_jar, runtime.jar, ...runtime.libraries].join(path.delimiter), mainClass, ...options];
}

// Adoption uses a separate JVM because OpenPnP Configuration is process-global.
// A failed or interrupted publication is inspected in its existing destination.
export async function runAdoptionProcess(java, args) {
  const child = spawn(java, args, { env: javaEnvironment().environment, stdio: ['ignore', 'pipe', 'inherit'], shell: false });
  const output = []; let outputBytes = 0; let failure; let killTimer;
  const stop = reason => {
    failure ||= new Error(reason);
    child.kill('SIGTERM');
    killTimer ||= setTimeout(() => child.kill('SIGKILL'), 5000);
  };
  const cancelled = () => stop('Adoption interrupted; inspect the same destination before any further action.');
  const timer = setTimeout(() => stop('Adoption exceeded 60 seconds; inspect the same destination for its publication state.'), 60000);
  process.once('SIGINT', cancelled); process.once('SIGTERM', cancelled);
  child.stdout.on('data', chunk => {
    if (failure) return;
    outputBytes += chunk.length;
    if (outputBytes > 256 * 1024) stop('Native adoption output exceeded its bound.');
    else output.push(chunk);
  });
  try {
    const exit = await new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('close', (code, signal) => resolve({ code, signal }));
    });
    if (failure) throw failure;
    if (exit.code !== 0) throw new Error(`Native adoption failed (${exit.signal || `code ${exit.code}`}); inspect its retained destination evidence.`);
    let text;
    try { text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(output, outputBytes)); }
    catch { throw new Error('Native adoption completion is not valid UTF-8; inspect the same destination.'); }
    const results = text.split('\n').filter(line => line.startsWith('OPENPNP_CODEX_ADOPTED '));
    if (results.length !== 1) throw new Error('Native adoption returned no unique completion receipt; inspect the destination.');
    let result;
    try { result = JSON.parse(results[0].slice('OPENPNP_CODEX_ADOPTED '.length)); }
    catch { throw new Error('Native adoption completion receipt is malformed; inspect the same destination.'); }
    if (!result || typeof result !== 'object') throw new Error('Native adoption completion receipt is invalid; inspect the same destination.');
    if (result.adopted !== true || result.native_xml_roundtrip_verified !== true || result.scripts_activated !== false || result.physical_state_transferred !== false) throw new Error('Native adoption completion scope was not verified.');
    return result;
  } finally {
    clearTimeout(timer); clearTimeout(killTimer);
    process.removeListener('SIGINT', cancelled); process.removeListener('SIGTERM', cancelled);
  }
}

export async function adoptConfiguration({ stateDir, openpnpHome, java = 'java', bundle, expectedSha256, destination }) {
  if (!bundle || !path.isAbsolute(bundle) || !destination || !path.isAbsolute(destination)) throw new Error('--bundle and --destination must be absolute paths.');
  if (!/^[a-f0-9]{64}$/.test(expectedSha256 || '')) throw new Error('--sha256 must be the expected lowercase archive SHA-256 from the export receipt.');
  if (sha256(await readBoundedRegular(bundle, 32 * 1024 * 1024)) !== expectedSha256) throw new Error('Portable archive SHA-256 does not match the supplied export receipt.');
  const runtime = await verifiedRuntime({ stateDir, openpnpHome });
  const canonicalBundle = await realpath(bundle);
  const canonicalDestination = path.join(await realpath(path.dirname(destination)), path.basename(destination));
  separateTrees(canonicalDestination, runtime.nativeRoot, 'Adoption destination and OpenPnP runtime');
  separateTrees(canonicalDestination, await realpath(stateDir), 'Adoption destination and source operational state');
  separateTrees(canonicalDestination, await realpath(pluginRoot), 'Adoption destination and plugin package');
  return runAdoptionProcess(java, nativeArguments(runtime, 'org.openpnp.codex.NativePortableConfigurationMain',
    ['--bundle', canonicalBundle, '--sha256', expectedSha256, '--destination', canonicalDestination]));
}

export async function launchAdoptedSimulator({ stateDir, openpnpHome, java = 'java', port = '0', adoptionDir }) {
  if (!stateDir || !path.isAbsolute(stateDir)) throw new Error('Choose an explicit absolute --state-dir that does not yet exist for the adopted simulator.');
  if (!adoptionDir || !path.isAbsolute(adoptionDir)) throw new Error('--adoption-dir must be an absolute path to a completed adoption.');
  if (!/^\d+$/.test(port) || Number(port) > 65535) throw new Error('--port must be 0–65535.');
  const adopted = await realpath(adoptionDir);
  if (!(await lstat(adopted)).isDirectory()) throw new Error('Adoption directory must be a directory.');
  const fresh = path.join(await realpath(path.dirname(stateDir)), path.basename(stateDir));
  separateTrees(fresh, adopted, 'Adopted configuration and new operational state');
  try { await lstat(fresh); throw Object.assign(new Error('New operational state already exists; preserve its contents.'), { code: 'EEXIST' }); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (!openpnpHome || !path.isAbsolute(openpnpHome)) throw new Error('--openpnp-home must be an absolute path to the pinned OpenPnP build.');
  const nativeRoot = await realpath(openpnpHome);
  const packagedRoot = await realpath(pluginRoot);
  separateTrees(fresh, nativeRoot, 'New operational state and OpenPnP runtime');
  separateTrees(adopted, nativeRoot, 'Adoption directory and OpenPnP runtime');
  separateTrees(fresh, packagedRoot, 'New operational state and plugin package');
  separateTrees(adopted, packagedRoot, 'Adoption directory and plugin package');
  await verifiedRuntime({ openpnpHome, sourceRoot: pluginRoot });
  // Exclusive reservation prevents journal, token and connection replacement.
  await mkdir(fresh, { mode: 0o700 });
  await installBridge(fresh);
  return launchSimulator({ stateDir: fresh, openpnpHome, java, port, profile: 'adopted-simulator', adoptionDir: adopted });
}
export async function main(argv) {
  const { command, options } = parseArguments(argv);
  const stateDir = path.resolve(options['--state-dir'] || stateDirectory());
  if (command === 'help') return { usage: [
    'install-bridge [--state-dir PATH]', 'configure --url http://127.0.0.1:PORT/ --token-file ABSOLUTE_PATH [--state-dir PATH]',
    'doctor [--state-dir PATH] [--connection-file PATH]',
    'diagnostics [--state-dir PATH] [--output ABSOLUTE_JSON_PATH]',
    'support-export --output NEW_ABSOLUTE_TAR [--state-dir PATH] [--operation-ids UUID,UUID] [--artifacts UUID:SHA256:camera,UUID:SHA256:job-document]',
    'inspect-controller-history --journal ABSOLUTE_CLOSED_JOURNAL --openpnp-home VERIFIED_RUNTIME --java ABSOLUTE_JAVA [--state-dir INSTALLED_STATE]',
    'start-controller-simulator --state-dir NEW_ABSOLUTE_STATE --openpnp-home VERIFIED_RUNTIME [--java EXECUTABLE]',
    'start-simulator --openpnp-home ABSOLUTE_PATH [--java EXECUTABLE] [--state-dir PATH] [--port NUMBER] [--profile native-simulator|sustained-workload|vacuum-sensing] [--sensing-scenario success|missed-pick-retry|retained-after-place|lost-before-place|invalid-read]',
    'restart-gui-simulator --session-dir ABSOLUTE_ORIGINAL_GUI_SESSION --openpnp-home ABSOLUTE_PATCHED_RUNTIME [--java EXECUTABLE] [--state-dir PATH]',
    'start-gui-simulator --openpnp-home ABSOLUTE_PATCHED_RUNTIME [--java EXECUTABLE] [--state-dir PATH] [--profile gui-simulator|vacuum-sensing] [--sensing-scenario success|missed-pick-retry|retained-after-place|lost-before-place|invalid-read]',
    'adopt-configuration --bundle ABSOLUTE_ZIP --sha256 EXPORT_SHA256 --destination NEW_ABSOLUTE_DIRECTORY --openpnp-home ABSOLUTE_RUNTIME [--state-dir INSTALLED_STATE] [--java EXECUTABLE]',
    'start-adopted-simulator --adoption-dir ABSOLUTE_DIRECTORY --state-dir NEW_ABSOLUTE_STATE --openpnp-home ABSOLUTE_RUNTIME [--java EXECUTABLE] [--port NUMBER]',
    'uninstall-bridge --version VERSION [--state-dir PATH]',
  ], note: 'Persistent journals and user configuration are retained on uninstall. This release operates the isolated native simulator. Support export is offline; explicitly selected camera/document artifacts are included verbatim.' };
  if (command === 'inspect-controller-history') {
    const { inspectControllerHistory } = await import('./controller-history.mjs');
    return inspectControllerHistory({ stateDir, openpnpHome: options['--openpnp-home'], java: options['--java'], journal: options['--journal'] });
  }
  if (command === 'configure') return configure({ stateDir, url: options['--url'], tokenFile: options['--token-file'] });
  if (command === 'install-bridge') return installBridge(stateDir);
  if (command === 'doctor') return doctor(stateDir, options['--connection-file']);
  if (command === 'support-export') {
    const selections = supportSelections(options);
    const { exportSupportBundle } = await import('./support-export.mjs');
    return exportSupportBundle({ stateDir, output: options['--output'], ...selections });
  }
  if (command === 'diagnostics') {
    const output = options['--output'];
    if (output) {
      if (!path.isAbsolute(output)) throw new Error('--output must be an absolute path.');
      const stateRoot = await realpath(stateDir);
      const parent = await realpath(path.dirname(output));
      if (parent === stateRoot || parent.startsWith(stateRoot + path.sep)) throw new Error('Write diagnostics outside the machine state directory to protect its history.');
    }
    const result = await inspectJournal(stateDir);
    if (!options['--output']) return result;
    const temporary = `${output}.${randomUUID()}.tmp`;
    const handle = await open(temporary, 'wx', 0o600);
    try {
      try { await handle.writeFile(JSON.stringify(result, null, 2) + '\n'); await handle.sync(); } finally { await handle.close(); }
      await link(temporary, output);
    } finally { await rm(temporary, { force: true }); }
    return { written: output, action_replay_performed: false, may_authorize_action: false };
  }
  if (command === 'start-controller-simulator') {
    return launchControllerSimulator({ stateDir: options['--state-dir'], openpnpHome: options['--openpnp-home'], java: options['--java'] });
  }
  if (command === 'start-simulator') return launchSimulator({ stateDir, openpnpHome: options['--openpnp-home'], java: options['--java'], port: options['--port'], profile: options['--profile'], sensingScenario: options['--sensing-scenario'] });
  if (command === 'restart-gui-simulator') return launchRestartGuiSimulator({ stateDir, openpnpHome: options['--openpnp-home'], java: options['--java'], sessionDir: options['--session-dir'] });
  if (command === 'start-gui-simulator') return launchGuiSimulator({ stateDir, openpnpHome: options['--openpnp-home'], java: options['--java'], profile: options['--profile'], sensingScenario: options['--sensing-scenario'] });
  if (command === 'adopt-configuration') return adoptConfiguration({ stateDir, openpnpHome: options['--openpnp-home'], java: options['--java'], bundle: options['--bundle'], expectedSha256: options['--sha256'], destination: options['--destination'] });
  if (command === 'start-adopted-simulator') return launchAdoptedSimulator({ stateDir: options['--state-dir'], openpnpHome: options['--openpnp-home'], java: options['--java'], port: options['--port'], adoptionDir: options['--adoption-dir'] });
  if (command === 'uninstall-bridge') {
    const version = options['--version'];
    if (!/^\d+\.\d+\.\d+$/.test(version || '')) throw new Error('Choose an explicit installed semantic version.');
    let connection;
    try { connection = await readConnection(path.join(stateDir, 'connection.json')); }
    catch (error) { if (error.code !== 'ENOENT') throw new Error('Cannot verify the configured bridge is stopped. Repair the connection file before uninstalling.'); }
    if (connection) {
      let stopped = false;
      try { await fetch(new URL('/health', endpoint(connection.url)), { redirect: 'error', signal: AbortSignal.timeout(2000) }); }
      catch (error) { stopped = error.cause?.code === 'ECONNREFUSED'; }
      if (!stopped) throw new Error('Stop the connected simulator before uninstalling the bridge; unavailable authentication or an uncertain connection does not prove it stopped.');
    }
    const directory = path.join(stateDir, 'bridge', version);
    const entries = await readdir(directory);
    if (entries.some(name => !['openpnp-codex-bridge.jar', 'build-manifest.json'].includes(name))) throw new Error('Unexpected files in the installation; refusing automatic removal.');
    await rm(directory, { recursive: true });
    return { uninstalled: version, journals_preserved: true, configuration_preserved: true };
  }
}
let isMain = false;
try { isMain = Boolean(process.argv[1]) && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url); } catch { /* Importing helpers does not invoke the CLI. */ }
if (isMain) {
  try { const result = await main(process.argv.slice(2)); if (result !== undefined) { process.stdout.write(JSON.stringify(result, null, 2) + '\n'); if (result.mode === 'offline-recorded-controller-history' && result.valid === false) process.exitCode = 2; } }
  catch (error) { process.stderr.write(JSON.stringify({ error: error.message, ...(typeof error.code === 'string' ? { code: error.code } : {}) }) + '\n'); process.exitCode = 1; }
}
