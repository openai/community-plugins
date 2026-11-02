// SPDX-License-Identifier: Apache-2.0
// Offline-only launcher. Does not import the main CLI or construct native machinery.
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { constants } from 'node:fs';
import { lstat, open } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const UPSTREAM = '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c';
const REDUCER_SOURCE = 'src/openpnp/java/org/openpnp/codex/NativeControllerJournal.java';
const JOURNAL_JSON_SOURCE = 'src/openpnp/java/org/openpnp/codex/NativeJournalJson.java';
const MAIN = 'org.openpnp.codex.ControllerHistoryMain';
const MODE = 'offline-recorded-controller-history';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const validHash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
function need(value) { if (!value) throw new Error('OFFLINE_HISTORY_INPUT_OR_ARTIFACT_FAILURE'); }
function refusal(code) {
  return { schema_version: 1, mode: MODE, valid: false, error_code: code,
    native_authority_restored: false, native_machine_opened: false, controller_connection_opened: false,
    journal_appended: false, physical_qualification: false };
}
async function regular(file, max) {
  need(typeof file === 'string' && path.isAbsolute(file));
  for (let part = file; ; part = path.dirname(part)) {
    need(!(await lstat(part)).isSymbolicLink());
    if (part === path.dirname(part)) break;
  }
  const info = await lstat(file);
  need(info.isFile() && info.size <= max);
  return info;
}
async function bounded(file, max) {
  await regular(file, max);
  const fd = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  try {
    need((await fd.stat()).isFile());
    let total = 0; const chunks = [];
    for (;;) {
      const buffer = Buffer.alloc(Math.min(65536, max + 1 - total));
      const { bytesRead } = await fd.read(buffer, 0, buffer.length, null);
      if (!bytesRead) break;
      total += bytesRead; need(total <= max); chunks.push(buffer.subarray(0, bytesRead));
    }
    return Buffer.concat(chunks, total);
  } finally { await fd.close(); }
}
async function json(file) { return JSON.parse((await bounded(file, 1024 * 1024)).toString('utf8')); }

// The child is one owned JVM. Output limits apply while streaming. Wait for reaping;
// SIGKILL bounds cleanup after TERM, including a child that ignores TERM.
async function run(java, args) {
  const start = performance.now();
  const env = { PATH: process.platform === 'win32' ? (process.env.PATH || '') : '/usr/bin:/bin', LANG: 'C', LC_ALL: 'C' };
  if (process.platform === 'win32' && process.env.SystemRoot) env.SystemRoot = process.env.SystemRoot;
  return new Promise(resolve => {
    const child = spawn(java, args, { env, shell: false, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true, detached: process.platform !== 'win32' });
    let stdout = '', stderrBytes = 0, reason, killTimer, pipeTimer, complete = false;
    const signal = value => { try { if (process.platform !== 'win32' && child.pid) process.kill(-child.pid, value); else child.kill(value); } catch (error) { if (error.code !== 'ESRCH') reason ||= 'INSPECTOR_CLEANUP_FAILED'; } };
    const terminate = code => {
      reason ||= code; signal('SIGTERM');
      killTimer ||= setTimeout(() => signal('SIGKILL'), 250);
    };
    const timer = setTimeout(() => terminate('INSPECTION_DEADLINE'), 15000);
    const interrupted = () => terminate('INSPECTION_INTERRUPTED');
    process.once('SIGTERM', interrupted); process.once('SIGINT', interrupted);
    child.stdout.on('data', chunk => {
      if (Buffer.byteLength(stdout) + chunk.length > 65536) terminate('INSPECTION_OUTPUT_LIMIT');
      else stdout += chunk.toString('utf8');
    });
    child.stderr.on('data', chunk => { stderrBytes += chunk.length; if (stderrBytes > 4096) terminate('INSPECTION_OUTPUT_LIMIT'); });
    const finish = (code, signal) => {
      if (complete) return; complete = true;
      clearTimeout(timer); clearTimeout(killTimer); clearTimeout(pipeTimer);
      process.removeListener('SIGTERM', interrupted); process.removeListener('SIGINT', interrupted);
      resolve({ stdout, stderrBytes, reason, lifecycle: { pid: child.pid ?? null, exit_code: code, signal,
        elapsed_ms: Math.round(performance.now() - start), reaped: Boolean(child.pid) && (code !== null || signal !== null) } });
    };
    child.once('error', () => { reason ||= 'INSPECTOR_START_FAILED'; });
    child.once('exit', () => {
      pipeTimer = setTimeout(() => { terminate('INSPECTOR_PIPE_RETAINED'); }, 250);
    });
    child.once('close', finish);
  });
}

export async function inspectControllerHistory({ stateDir, openpnpHome, java, journal }) {
  try {
    // These reads use no credentials, discovery server, operational configuration, or writable state.
    need(typeof stateDir === 'string' && path.isAbsolute(stateDir));
    need(typeof openpnpHome === 'string' && path.isAbsolute(openpnpHome));
    need(typeof java === 'string' && path.isAbsolute(java));
    const journalInfo = await regular(journal, 64 * 1024 * 1024); need(journalInfo.size > 0);
    const helperRoot = path.join(ROOT, 'controller-history');
    const helperManifestFile = path.join(helperRoot, 'build-manifest.json');
    const helperManifestBytes = await bounded(helperManifestFile, 65536);
    const helper = JSON.parse(helperManifestBytes.toString('utf8'));
    need(helper.schema_version === 2 && helper.main_class === MAIN && helper.upstream_commit === UPSTREAM);
    for (const key of ['helper_sha256', 'source_sha256', 'reducer_class_sha256', 'reducer_source_sha256', 'gson_sha256', 'source_archive_sha256', 'journal_json_class_sha256', 'journal_json_source_sha256', 'built_against_bridge_sha256', 'runtime_manifest_sha256']) need(validHash(helper[key]));
    const installation = await json(path.join(stateDir, 'installation.json'));
    need(installation.version === '0.1.0' && installation.upstream_commit === UPSTREAM && validHash(installation.bridge_sha256));
    const installedManifestFile = path.join(path.dirname(installation.bridge_jar), 'build-manifest.json');
    const installed = await json(installedManifestFile);
    const packaged = await json(path.join(ROOT, 'bridge/build-manifest.json'));
    need(installed.upstream_commit === UPSTREAM && installed.bridge_version === '0.1.0' &&
      installed.bridge_sha256 === installation.bridge_sha256 && installed.bridge_sha256 === packaged.bridge_sha256 &&
      installed.bridge_sha256 === helper.built_against_bridge_sha256 && installed.runtime_manifest_sha256 === helper.runtime_manifest_sha256 &&
      installed.production_source_sha256?.[REDUCER_SOURCE] === helper.reducer_source_sha256 &&
      installed.production_source_sha256?.[JOURNAL_JSON_SOURCE] === helper.journal_json_source_sha256 &&
      validHash(installed.runtime_manifest_sha256) && validHash(installed.patched_native_jar_sha256));
    const runtimeManifestFile = path.join(openpnpHome, 'codex-build-manifest.json');
    const runtimeBytes = await bounded(runtimeManifestFile, 1024 * 1024);
    need(hash(runtimeBytes) === installed.runtime_manifest_sha256);
    const runtime = JSON.parse(runtimeBytes.toString('utf8'));
    need(runtime.upstream_commit === UPSTREAM && Array.isArray(runtime.files) && runtime.files.length > 0 && runtime.files.length <= 4096);
    const gsonRelative = 'lib/gson-2.2.3.jar';
    const gsonEntries = runtime.files.filter(entry => entry?.path === gsonRelative);
    need(gsonEntries.length === 1 && gsonEntries[0].sha256 === helper.gson_sha256);
    const nativeEntries = runtime.files.filter(entry => entry?.path === runtime.gui_jar);
    need(nativeEntries.length === 1 && nativeEntries[0].sha256 === installed.patched_native_jar_sha256);
    const jar = path.join(helperRoot, 'openpnp-controller-history.jar');
    const gson = path.join(openpnpHome, gsonRelative);
    const source = path.join(helperRoot, 'source/org/openpnp/codex/ControllerHistoryMain.java');
    const archive = path.join(helperRoot, 'corresponding-source.zip');
    const bindings = [[jar, helper.helper_sha256], [source, helper.source_sha256], [archive, helper.source_archive_sha256],
      [installation.bridge_jar, installed.bridge_sha256], [gson, helper.gson_sha256]];
    for (const [file, expected] of bindings) need(hash(await bounded(file, 64 * 1024 * 1024)) === expected);
    const javaHash = hash(await bounded(java, 64 * 1024 * 1024));
    const classpath = [jar, installation.bridge_jar, gson];
    need(classpath.every(file => !file.includes(path.delimiter)));
    const args = ['-Xmx128m', '-XX:+ExitOnOutOfMemoryError', '-Dfile.encoding=UTF-8', '-Djava.awt.headless=true',
      '-cp', classpath.join(path.delimiter), MAIN, '--journal', journal,
      '--expected-reducer-sha256', helper.reducer_class_sha256, '--expected-journal-json-sha256', helper.journal_json_class_sha256, '--expected-gson-sha256', helper.gson_sha256];
    const processResult = await run(java, args);
    const finish = result => ({ ...result, inspector_process: processResult.lifecycle });
    if (processResult.reason) return finish(refusal(processResult.reason));
    if (processResult.stderrBytes || ![0, 2].includes(processResult.lifecycle.exit_code)) return finish(refusal('INSPECTOR_PROCESS_FAILED'));
    const result = JSON.parse(processResult.stdout);
    need(result.schema_version === 1 && result.mode === MODE && typeof result.valid === 'boolean');
    need(result.valid === (processResult.lifecycle.exit_code === 0));
    for (const key of ['native_authority_restored', 'native_machine_opened', 'controller_connection_opened', 'journal_appended', 'physical_qualification']) need(result[key] === false);
    if (!result.valid) return finish(result);
    need(result.provenance?.bridge_sha256 === installed.bridge_sha256 && result.provenance?.reducer_class_sha256 === helper.reducer_class_sha256 && result.provenance?.journal_json_class_sha256 === helper.journal_json_class_sha256 && result.provenance?.gson_sha256 === helper.gson_sha256);
    // Keep selected runtime/code provenance bound for the complete inspection.
    for (const [file, expected] of [...bindings, [java, javaHash], [runtimeManifestFile, installed.runtime_manifest_sha256], [helperManifestFile, hash(helperManifestBytes)]]) need(hash(await bounded(file, 64 * 1024 * 1024)) === expected);
    return finish({ ...result, cli_provenance: { helper_manifest_sha256: hash(helperManifestBytes), helper_sha256: helper.helper_sha256,
      source_sha256: helper.source_sha256, journal_json_source_sha256: helper.journal_json_source_sha256, journal_json_class_sha256: helper.journal_json_class_sha256, source_archive_sha256: helper.source_archive_sha256, java_sha256: javaHash,
      runtime_manifest_sha256: installed.runtime_manifest_sha256, gson_sha256: helper.gson_sha256,
      runtime_scope: 'manifest binding and loaded Gson only; native application is not loaded' } });
  } catch { return refusal('OFFLINE_HISTORY_INPUT_OR_ARTIFACT_FAILURE'); }
}
