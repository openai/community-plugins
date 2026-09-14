// SPDX-License-Identifier: Apache-2.0
// Explicit saved simulator restart. No source preparation, automatic lease or recovery decision.
import { createHash, randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, mkdtemp, open, lstat, realpath, readdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { verifyGuiRuntime, launchPreparedGuiSimulator } from './gui-launcher.mjs';
import { javaEnvironment } from './java-environment.mjs';

const UPSTREAM = '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c';
const PATCH = 'a1de28f8907ef703280615621c87434c1853fd622696f73f38b2fd8477815079';
const PROFILE = 'native-gui-source-absent-restart-v1';
const PREPARED = 'prepared-native-gui-vacuum-fixture-v1';
const hash = data => createHash('sha256').update(data).digest('hex');
const isHash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const uuid = value => typeof value === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
const relative = value => typeof value === 'string' && value.length > 0 && !path.isAbsolute(value) && !value.includes('\\') && value.split('/').every(p => p && p !== '.' && p !== '..');
const scenarios = ['success', 'missed-pick-retry', 'retained-after-place', 'lost-before-place', 'invalid-read'];
async function directory(file, privateOnly = true) {
  const info = await lstat(file);
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid() || (info.mode & (privateOnly ? 0o077 : 0o022)) || await realpath(file) !== file) throw new Error('Restart requires existing canonical private directories owned by the current user.');
}
async function bytes(file, max, privateOnly = true) {
  if (await realpath(file) !== file) throw new Error('Restart inputs cannot contain symbolic links or noncanonical paths.');
  const handle = await open(file, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const before = await handle.stat();
    if (!before.isFile() || before.uid !== process.getuid() || (before.mode & (privateOnly ? 0o077 : 0o022)) || before.size > max) throw new Error('Restart requires bounded private regular input files.');
    const chunks = []; let total = 0;
    for (;;) { const chunk = Buffer.alloc(Math.min(65536, max + 1 - total)); const { bytesRead } = await handle.read(chunk);
      if (!bytesRead) break; total += bytesRead; if (total > max) throw new Error('Restart input grew beyond its bound.'); chunks.push(chunk.subarray(0, bytesRead)); }
    const after = await handle.stat();
    if (after.size !== before.size || after.mtimeMs !== before.mtimeMs || total !== before.size) throw new Error('Restart input changed during verification.');
    return Buffer.concat(chunks, total);
  } finally { await handle.close(); }
}
async function writeNew(file, record) {
  const handle = await open(file, 'wx', 0o600); try { await handle.writeFile(JSON.stringify(record, null, 2) + '\n'); await handle.sync(); } finally { await handle.close(); }
  const parent = await open(path.dirname(file), 'r'); try { await parent.sync(); } finally { await parent.close(); }
}
function exactKeys(record, keys, label) {
  if (!record || typeof record !== 'object' || Array.isArray(record) || Object.keys(record).sort().join('|') !== [...keys].sort().join('|')) throw new Error(`Unsupported ${label} schema.`);
}

// This closed subset is a pre-class-loading check, not an XML object loader or native attestation.
const classes = new Set([
  'java.lang.Boolean', 'java.util.ArrayList', 'java.util.HashMap', 'java.util.HashSet',
  'org.openpnp.machine.reference.ReferenceMachine', 'org.openpnp.machine.reference.ReferenceHead',
  'org.openpnp.machine.reference.ReferenceNozzle', 'org.openpnp.machine.reference.ReferenceNozzleTip',
  'org.openpnp.machine.reference.ReferenceActuator', 'org.openpnp.machine.reference.ReferencePnpJobProcessor',
  'org.openpnp.machine.reference.ReferencePnpJobProcessor$SimplePnpJobPlanner',
  'org.openpnp.machine.reference.axis.ReferenceControllerAxis', 'org.openpnp.machine.reference.axis.ReferenceVirtualAxis',
  'org.openpnp.machine.reference.camera.ImageCamera', 'org.openpnp.machine.reference.camera.SimulatedUpCamera',
  'org.openpnp.machine.reference.camera.AutoFocusProvider', 'org.openpnp.machine.reference.driver.NullDriver',
  'org.openpnp.machine.reference.driver.NullMotionPlanner', 'org.openpnp.machine.reference.feeder.ReferenceTrayFeeder',
  'org.openpnp.machine.reference.vision.OpenCvVisionProvider', 'org.openpnp.machine.reference.vision.ReferenceBottomVision',
  'org.openpnp.machine.reference.vision.ReferenceFiducialLocator',
  'org.openpnp.model.BottomVisionSettings', 'org.openpnp.model.FiducialVisionSettings',
  ...['ConvertColor', 'CreateFootprintTemplateImage', 'DetectRectlinearSymmetry', 'DrawContours', 'DrawKeyPoints', 'DrawRotatedRects', 'DrawTemplateMatches', 'FilterContours', 'FindContours', 'ImageRecall', 'MaskCircle', 'MaskHsv', 'MatchTemplate', 'MinAreaRect', 'ParameterBool', 'ParameterNumeric', 'Threshold', 'BlurGaussian', 'ConvertModelToKeyPoints', 'DetectCircularSymmetry', 'DrawCircles', 'ImageCapture', 'ImageWriteDebug'].map(name => `org.openpnp.vision.pipeline.stages.${name}`),
]);
const names = '[A-Za-z_][A-Za-z0-9_.-]*';
export function validateSavedSimulatorXml(input, machine = false) {
  const xml = new TextDecoder('utf-8', { fatal: true }).decode(input);
  if (xml.includes('\0') || /<!|<\?(?!xml\s)/.test(xml)) throw new Error('Unsupported saved XML declaration, entity or executable markup.');
  const stack = []; let offset = 0, roots = 0, machines = 0, drivers = 0, planners = 0, root;
  while (offset < xml.length) {
    const start = xml.indexOf('<', offset), text = xml.slice(offset, start === -1 ? undefined : start);
    if ((!stack.length && text.trim()) || /&(?!(?:amp|lt|gt|quot|apos|#[0-9]+|#x[a-fA-F0-9]+);)/.test(text)) throw new Error('Unsupported saved XML text.');
    if (stack.at(-1) === 'source-uri' && text.trim() !== 'classpath://samples/pnp-test/pnp-test.png') throw new Error('Restart cameras must use the pinned simulator image resource.');
    if (['home-after-enabled', 'pool-scripting-engines'].includes(stack.at(-1)) && text.trim() !== 'false') throw new Error('Restart cannot enable automatic homing.');
    if (start === -1) break;
    const end = xml.indexOf('>', start); if (end < 0) throw new Error('Unterminated saved XML tag.');
    const tag = xml.slice(start, end + 1); offset = end + 1;
    if (tag.startsWith('<?')) { if (start !== xml.search(/\S/) || !/^<\?xml\s+version="1\.0"(?:\s+encoding="UTF-8")?\s*\?>$/.test(tag)) throw new Error('Unsupported XML declaration.'); continue; }
    const close = new RegExp(`^</(${names})\\s*>$`).exec(tag);
    if (close) { if (stack.pop() !== close[1]) throw new Error('Mismatched saved XML tags.'); continue; }
    const opened = new RegExp(`^<(${names})([\\s\\S]*?)(/?)>$`).exec(tag); if (!opened) throw new Error('Unsupported saved XML tag.');
    const [, name, raw, self] = opened, attributes = new Map(); let rest = raw;
    while (rest.trim()) { const match = new RegExp(`^\\s+(${names})\\s*=\\s*("[^"<>]*"|'[^'<>]*')`).exec(rest); if (!match || attributes.has(match[1])) throw new Error('Unsupported or duplicate saved XML attribute.'); attributes.set(match[1], match[2].slice(1, -1)); rest = rest.slice(match[0].length); }
    const clazz = attributes.get('class');
    if (clazz !== undefined && !classes.has(clazz)) throw new Error(`Unsupported simulator serialized class: ${clazz}`);
    if (attributes.has('resolves-to') || (/script/i.test(name) && name !== 'pool-scripting-engines' && !(name === 'openpnp-script-state' && self && !attributes.size)) || [...attributes.keys()].some(key => /(?:^|-)script(?:ing)?(?:-|$)|(?:^|-)class$/i.test(key) && key !== 'class')) throw new Error('Executable or polymorphic saved configuration is unsupported.');
    if (name === 'machine') { if (clazz !== 'org.openpnp.machine.reference.ReferenceMachine') throw new Error('Expected the exact simulator machine.'); machines++; }
    if (name === 'driver') { if (clazz !== 'org.openpnp.machine.reference.driver.NullDriver') throw new Error('Only the exact NullDriver simulator is supported.'); drivers++; }
    if (name === 'motion-planner') { if (clazz !== 'org.openpnp.machine.reference.driver.NullMotionPlanner') throw new Error('Only the exact NullMotionPlanner is supported.'); planners++; }
    if (name === 'source-uri' && self) throw new Error('A simulator image resource is required.');
    if (!stack.length) { roots++; root = name; } if (!self) stack.push(name);
  }
  if (stack.length || roots !== 1 || (machine && (root !== 'openpnp-machine' || machines !== 1 || drivers !== 1 || planners !== 1))) throw new Error('Saved configuration is outside the closed single-NullDriver simulator profile.');
}

function validateEmptyLibrary(data, kind) {
  // Configuration.loadBoards/loadPanels follows File entries outside config (and panels
  // recursively follow children). This startup profile admits no such external load graph.
  const xml = new TextDecoder('utf-8', { fatal: true }).decode(data).trim();
  const root = `openpnp-${kind}`;
  if (!new RegExp(`^<${root}\\s*/>$|^<${root}>\\s*(?:<${kind}\\s*/>|<${kind}>\\s*</${kind}>)?\\s*</${root}>$`).test(xml))
    throw new Error('Restart requires empty native board and panel libraries; external library graphs are not admitted by this launcher profile.');
}

async function currentFiles(config, bootstrap, bootstrapHash) {
  const files = []; let total = 0, entries = 0;
  async function walk(dir) { await directory(dir, dir === config); for (const item of await readdir(dir, { withFileTypes: true })) {
    if (++entries > 4096) throw new Error('Restart configuration inventory is too large.');
    const file = path.join(dir, item.name);
    if (item.isSymbolicLink()) throw new Error('Restart configuration cannot contain symbolic links.');
    if (item.isDirectory()) { await walk(file); continue; }
    const data = await bytes(file, 8 * 1024 * 1024, false), relativePath = path.relative(config, file).split(path.sep).join('/');
    total += data.length; if (++files.length > 256 || total > 32 * 1024 * 1024) throw new Error('Restart configuration exceeds native attestation bounds.');
    files[files.length - 1] = { path: relativePath, bytes: data.length, sha256: hash(data) };
    if (relativePath.startsWith('scripts/') && (file !== bootstrap || hash(data) !== bootstrapHash)) throw new Error('Restart refuses active scripts other than the exact verified bootstrap.');
    if (relativePath.endsWith('.xml')) validateSavedSimulatorXml(data, relativePath === 'machine.xml');
    if (['boards.xml', 'panels.xml'].includes(relativePath)) validateEmptyLibrary(data, relativePath.slice(0, -4));
  } }
  await walk(config);
  for (const required of ['machine.xml', 'packages.xml', 'parts.xml', 'boards.xml', 'panels.xml', 'vision-settings.xml', 'script-state.xml']) if (!files.some(row => row.path === required)) throw new Error('All seven native saved configuration files are required; restart must not trigger defaults or automatic resave.');
  return files.sort((a, b) => a.path.localeCompare(b.path));
}
async function refuseActiveProcesses(config, nativeState) {
  // Legacy launches did not persist a PID. This conservative inventory is supplementary to
  // the exclusive launcher reservation and the authoritative native journal file lock.
  const { stdout } = await promisify(execFile)('/bin/ps', ['-ww', '-axo', 'pid=,command='], { timeout: 10000, maxBuffer: 8 * 1024 * 1024 });
  for (const row of stdout.split('\n')) { const match = /^\s*(\d+)\s+(.+)$/.exec(row); if (!match || Number(match[1]) === process.pid) continue;
    if ([`-DconfigDir=${config}`, `-Dopenpnp.codex.stateDir=${nativeState}`].some(arg => match[2].includes(arg))) throw new Error(`Another process may own this original simulator scope (PID ${match[1]}). Close it before restarting.`);
  }
}

/** Reserve and verify one exact historical GUI session. Caller must release after child exit. */
export async function prepareRestartGuiSimulator({ stateDir, openpnpHome, sessionDir, java = 'java', sourceRoot }) {
  if (!path.isAbsolute(sessionDir || '') || path.normalize(sessionDir) !== sessionDir) throw new Error('Use the absolute canonical original GUI session directory.');
  const runtime = await verifyGuiRuntime({ stateDir, openpnpHome, java, sourceRoot });
  const { receipt, installed, manifest, manifestPath, bridgeJar, jar, launcherJar, samples, libraries, inventory } = runtime;
  const capability = installed.gui_sensing_restart;
  exactKeys(capability, ['schema_version', 'profile', 'startup_mode', 'prepared_manifest_profile', 'source_installed_at_startup'], 'GUI restart capability');
  if (capability.schema_version !== 1 || capability.profile !== PROFILE || capability.startup_mode !== 'restart' || capability.prepared_manifest_profile !== PREPARED || capability.source_installed_at_startup !== false || manifest.gui_ownership.patch_sha256 !== PATCH) throw new Error('Install a bridge and native runtime explicitly compatible with source-absent restart and retained GUI graphs.');
  await directory(stateDir); await directory(sessionDir);
  const originalBytes = await bytes(path.join(sessionDir, 'launcher.json'), 1024 * 1024), original = JSON.parse(originalBytes);
  const config = path.join(sessionDir, 'config'), nativeState = path.join(sessionDir, 'bridge-state'), bootstrap = path.join(config, 'scripts/codex-bootstrap.js'), manifestFile = path.join(sessionDir, 'sensing-fixture.json');
  if (original.session_root !== sessionDir || original.config_directory !== config || original.native_state_directory !== nativeState || original.bootstrap_script !== bootstrap || original.profile !== 'vacuum-sensing' || original.sensing_preparation?.profile !== 'vacuum-sensing' || original.sensing_preparation?.manifest !== manifestFile || !scenarios.includes(original.sensing_preparation?.scenario) || !isHash(original.sensing_preparation?.sha256) || !isHash(original.bridge_sha256) || !isHash(original.runtime_manifest_sha256) || original.bootstrap_sha256 !== installed.bootstrap_sha256 || original.physical_qualification !== false || original.bridge_on_application_classpath !== false) throw new Error('Original receipt is not an exact prepared private GUI sensing session.');
  for (const dir of [config, path.join(config, 'scripts'), nativeState, path.join(nativeState, 'journal')]) await directory(dir);
  await refuseActiveProcesses(config, nativeState);
  const reservation = path.join(sessionDir, 'restart-reservation');
  try { await mkdir(reservation, { mode: 0o700 }); } catch (error) { if (error.code === 'EEXIST') throw new Error('Original session already has a restart reservation. Its owner must finish or be independently verified absent before manual stale-reservation removal.'); throw error; }
  let released = false; const owner = randomUUID();
  async function release() { if (released) return; const record = JSON.parse(await bytes(path.join(reservation, 'owner.json'), 16384)); if (record.owner !== owner) throw new Error('Restart reservation ownership changed.'); await rm(reservation, { recursive: true }); released = true; }
  try {
    await writeNew(path.join(reservation, 'owner.json'), { owner, launcher_pid: process.pid, session_directory: sessionDir, created_at: new Date().toISOString() });
    const preparedBytes = await bytes(manifestFile, 1024 * 1024), prepared = JSON.parse(preparedBytes);
    exactKeys(prepared, ['schema_version', 'profile', 'upstream_commit', 'config_directory', 'scenario', 'files', 'source_authority_created', 'simulation_only', 'hardware_qualified'], 'prepared fixture');
    if (hash(preparedBytes) !== original.sensing_preparation.sha256 || prepared.schema_version !== 1 || prepared.profile !== PREPARED || prepared.upstream_commit !== UPSTREAM || prepared.config_directory !== config || prepared.scenario !== original.sensing_preparation.scenario || prepared.source_authority_created !== false || prepared.simulation_only !== true || prepared.hardware_qualified !== false || !Array.isArray(prepared.files) || !prepared.files.length || prepared.files.length > 256) throw new Error('Original prepared fixture provenance is invalid.');
    let oldTotal = 0; const oldPaths = new Set(); for (const row of prepared.files) {
      exactKeys(row, ['path', 'bytes', 'sha256'], 'original inventory row');
      if (!relative(row.path) || oldPaths.has(row.path) || !isHash(row.sha256) || !Number.isInteger(row.bytes) || row.bytes < 0 || row.bytes > 8 * 1024 * 1024 || (oldTotal += row.bytes) > 32 * 1024 * 1024) throw new Error('Invalid original inventory.'); oldPaths.add(row.path);
    }
    if (hash(await bytes(bootstrap, 65536)) !== installed.bootstrap_sha256) throw new Error('Preserved bootstrap differs from the currently verified bootstrap; no file was replaced.');
    const token = (await bytes(path.join(nativeState, 'bridge.token'), 4096)).toString().trim();
    if (!/^[A-Za-z0-9_-]{32,256}$/.test(token)) throw new Error('Original bridge token is invalid.');
    const machineId = (await bytes(path.join(nativeState, 'journal/machine-id'), 256)).toString().trim(); if (!uuid(machineId)) throw new Error('Original machine identity must be canonical.');
    const events = await bytes(path.join(nativeState, 'journal/operations.jsonl'), 512 * 1024 * 1024); if (!events.length) throw new Error('Original operations journal must not be empty.');
    const files = await currentFiles(config, bootstrap, installed.bootstrap_sha256);
    const restarts = path.join(sessionDir, 'restart-attempts'); await mkdir(restarts, { mode: 0o700 }).catch(error => { if (error.code !== 'EEXIST') throw error; }); await directory(restarts);
    const sessionRoot = await mkdtemp(path.join(restarts, 'restart-')), preferences = path.join(sessionRoot, 'preferences'), home = path.join(sessionRoot, 'home');
    for (const dir of [preferences, path.join(preferences, 'user'), path.join(preferences, 'system'), home]) await mkdir(dir, { mode: 0o700 });
    const { environment, ignored } = javaEnvironment();
    const properties = { configDir: config, 'java.util.prefs.PreferencesFactory': 'org.openpnp.codex.IsolatedPreferencesFactory', 'java.util.prefs.userRoot': path.join(preferences, 'user'), 'java.util.prefs.systemRoot': path.join(preferences, 'system'), 'user.home': home,
      'openpnp.codex.bridgeJar': bridgeJar, 'openpnp.codex.bridgeSha256': receipt.bridge_sha256, 'openpnp.codex.stateDir': nativeState, 'openpnp.codex.sampleRoot': samples, 'openpnp.codex.bootstrapPath': bootstrap, 'openpnp.codex.bootstrapSha256': installed.bootstrap_sha256, 'openpnp.codex.runtimeManifest': manifestPath, 'openpnp.codex.runtimeManifestSha256': installed.runtime_manifest_sha256,
      'openpnp.codex.sensingStartup': 'restart', 'openpnp.codex.sensingManifest': manifestFile, 'openpnp.codex.sensingManifestSha256': original.sensing_preparation.sha256, 'openpnp.codex.sensingScenario': prepared.scenario };
    const args = ['-Xmx2g', '-XX:+ExitOnOutOfMemoryError', '-Djava.awt.headless=false', '--add-opens=java.base/java.lang=ALL-UNNAMED', '--add-opens=java.desktop/java.awt=ALL-UNNAMED', '--add-opens=java.desktop/java.awt.color=ALL-UNNAMED', ...(process.platform === 'darwin' ? ['--add-exports=java.desktop/com.apple.eawt=ALL-UNNAMED'] : []), ...Object.entries(properties).map(([key, value]) => `-D${key}=${value}`), '-cp', [launcherJar, jar, ...libraries].join(path.delimiter), 'org.openpnp.Main'];
    const result = { gui_session_id: path.basename(sessionRoot), session_root: sessionRoot, original_session_root: sessionDir, config_directory: config, native_state_directory: nativeState, bootstrap_script: bootstrap, preferences_directory: preferences, java, args,
      bridge_sha256: receipt.bridge_sha256, runtime_manifest_sha256: installed.runtime_manifest_sha256, bootstrap_sha256: installed.bootstrap_sha256, launcher_preferences_jar_sha256: inventory.get(manifest.gui_launcher_jar), bridge_on_application_classpath: false, profile: 'vacuum-sensing',
      sensing_restart: { profile: PROFILE, machine_id: machineId, original_launcher_sha256: hash(originalBytes), prepared_manifest_sha256: hash(preparedBytes), journal_prefix: { bytes: events.length, sha256: hash(events), history_validated: false }, current_files: files, source_installed_at_startup: false, execution_authority_restored: false },
      preferences_scope: 'fresh-restart-in-memory; not persisted', ignored_java_environment: ignored, connected: false, local_grant: false, state: 'restart-prepared', physical_qualification: false };
    await writeNew(path.join(sessionRoot, 'launcher.json'), result);
    // Recheck mutable inputs immediately before spawning. Native GUI capture and journal lock
    // are still required; this file observation never grants process or sensing authority.
    async function beforeLaunch() {
      await refuseActiveProcesses(config, nativeState);
      if (hash(await bytes(path.join(sessionDir, 'launcher.json'), 1024 * 1024)) !== hash(originalBytes) || hash(await bytes(manifestFile, 1024 * 1024)) !== hash(preparedBytes) || hash(await bytes(path.join(nativeState, 'journal/operations.jsonl'), 512 * 1024 * 1024)) !== hash(events) || (await bytes(path.join(nativeState, 'journal/machine-id'), 256)).toString().trim() !== machineId || (await bytes(path.join(nativeState, 'bridge.token'), 4096)).toString().trim() !== token || JSON.stringify(await currentFiles(config, bootstrap, installed.bootstrap_sha256)) !== JSON.stringify(files)) throw new Error('Original restart scope changed before Java startup.');
      const checked = await verifyGuiRuntime({ stateDir, openpnpHome, java, sourceRoot });
      if (JSON.stringify(checked.installed) !== JSON.stringify(installed) || JSON.stringify(checked.manifest) !== JSON.stringify(manifest) || checked.bridgeJar !== bridgeJar) throw new Error('Verified restart runtime changed before Java startup.');
    }
    return { ...result, stateDir, environment, beforeLaunch, release };
  } catch (error) { try { await release(); } catch { /* Retain reservation when owner creation or cleanup is uncertain. */ } throw error; }
}

export async function launchRestartGuiSimulator(options) {
  const prepared = await prepareRestartGuiSimulator(options);
  try { return await launchPreparedGuiSimulator(prepared); } catch (error) {
    try { await writeNew(path.join(prepared.session_root, 'launcher-result.json'), { state: 'launcher-failed', error: error.message, connected: false, evidence_preserved: true, local_grant_requested_by_launcher: false }); } catch { /* Existing lifecycle receipt remains authoritative. */ }
    throw error;
  } finally { await prepared.release(); }
}
