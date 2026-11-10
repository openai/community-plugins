/** Optional native exports. Native coordinates stay native; this is not a canonical importer. */
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';

export class KicadError extends Error {
  constructor(code, message, details = {}) { super(message); this.name = 'KicadError'; this.code = code; this.details = details; }
}
const fail = (code, message, details) => { throw new KicadError(code, message, details); };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const exists = file => { try { fs.lstatSync(file); return true; } catch (e) { if (e.code === 'ENOENT') return false; throw e; } };
const stringify = value => JSON.stringify(value, null, 2) + '\n';
const MAX_INPUT = 25 * 1024 * 1024;
const MAX_OUTPUT = 64 * 1024 * 1024;
const MAX_TOTAL_OUTPUT = 128 * 1024 * 1024;
const TIMEOUT = 60_000;
const MAX_BUFFER = 2 * 1024 * 1024;

export function findKicad(explicitPath) {
  if (explicitPath !== undefined && (typeof explicitPath !== 'string' || !explicitPath.trim() || explicitPath.includes('\0'))) return { available: false, bin: null, version: null, reason: 'Explicit KiCad CLI path must be a nonempty file path.' };
  const name = process.platform === 'win32' ? 'kicad-cli.exe' : 'kicad-cli';
  const candidates = explicitPath !== undefined ? [path.resolve(explicitPath)] : [
    ...(process.env.PATH ?? '').split(path.delimiter).filter(Boolean).map(dir => path.join(dir, name)),
    '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli',
  ];
  let reason = 'No working KiCad CLI was found. Supply its executable path, or continue with supplied JSON/CSV exports.';
  for (const candidate of [...new Set(candidates)]) {
    try {
      if (!fs.statSync(candidate).isFile()) continue;
      fs.accessSync(candidate, fs.constants.X_OK);
      const bin = fs.realpathSync(candidate);
      const result = spawnSync(bin, ['version'], { encoding: 'utf8', timeout: 10_000, maxBuffer: 64 * 1024, killSignal: 'SIGKILL', windowsHide: true, shell: false, stdio: ['ignore', 'pipe', 'pipe'] });
      const version = (result.stdout ?? '').trim();
      if (!result.error && result.status === 0 && version && version.length <= 2048) return { available: true, bin, version };
      reason = result.error ? `KiCad version check failed: ${result.error.code ?? result.error.message}.` : `KiCad version check did not return a usable version (exit ${result.status}).`;
    } catch (e) { reason = `KiCad executable is unavailable: ${e.code ?? e.message}.`; }
  }
  return { available: false, bin: explicitPath === undefined ? null : path.resolve(explicitPath), version: null, reason };
}

function sourceFile(file, extension, label) {
  if (typeof file !== 'string' || !file.trim() || file.includes('\0')) fail('INVALID_NATIVE_INPUT', `${label} must be a file path.`);
  const resolved = path.resolve(file);
  if (path.extname(resolved).toLowerCase() !== extension) fail('INVALID_NATIVE_EXTENSION', `${label} must have the ${extension} extension.`);
  let stat;
  try { stat = fs.lstatSync(resolved); }
  catch (e) { fail('INVALID_NATIVE_INPUT', `${label} cannot be read.`, { path: resolved, cause: e.code }); }
  if (!stat.isFile() || stat.isSymbolicLink()) fail('INVALID_NATIVE_INPUT', `${label} must be a regular file, not a symlink or directory.`, { path: resolved });
  if (!stat.size || stat.size > MAX_INPUT) fail('NATIVE_INPUT_LIMIT', `${label} must be nonempty and at most 25 MiB.`, { path: resolved });
  const data = fs.readFileSync(resolved);
  return { path: fs.realpathSync(resolved), sha256: hash(data), bytes: data.length };
}

function sourceState(sources) {
  return sources.map(source => {
    try {
      const stat = fs.lstatSync(source.path);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_INPUT) return { ...source, unchanged: false, afterSha256: null };
      const afterSha256 = hash(fs.readFileSync(source.path));
      return { ...source, afterSha256, unchanged: source.sha256 === afterSha256 };
    } catch { return { ...source, afterSha256: null, unchanged: false }; }
  });
}

function outputFiles(root) {
  const files = [];
  let total = 0;
  function visit(directory, depth = 0) {
    if (depth > 12) fail('NATIVE_OUTPUT_LIMIT', 'Native output directory nesting exceeds 12 levels.');
    for (const name of fs.readdirSync(directory).sort()) {
      if (directory === root && ['native-export.json', '.kicad-config'].includes(name)) continue;
      const file = path.join(directory, name), stat = fs.lstatSync(file);
      if (stat.isSymbolicLink() || (!stat.isFile() && !stat.isDirectory())) fail('UNSAFE_NATIVE_OUTPUT', 'Native exports must contain regular files and directories, without symlinks.');
      if (stat.isDirectory()) { visit(file, depth + 1); continue; }
      if (stat.size > MAX_OUTPUT || (total += stat.size) > MAX_TOTAL_OUTPUT || files.length >= 1000) fail('NATIVE_OUTPUT_LIMIT', 'Native output exceeds the bounded file/size limit.');
      const data = fs.readFileSync(file);
      files.push({ path: path.relative(root, file).split(path.sep).join('/'), sha256: hash(data), bytes: data.length });
    }
  }
  visit(root);
  return files;
}

function assertExports(files, needsBom) {
  const hasFile = name => files.some(f => f.path === name && f.bytes > 0);
  if (!hasFile('positions.csv') || (needsBom && !hasFile('bom.csv'))) fail('NATIVE_EXPORT_MISSING', 'KiCad returned success without the requested nonempty CSV export.');
  for (const side of ['front', 'back']) {
    if (!files.some(f => f.path.startsWith(`${side}/`) && f.path.toLowerCase().endsWith('.svg') && f.bytes > 0)) fail('NATIVE_EXPORT_MISSING', `KiCad returned success without a nonempty ${side} SVG.`);
  }
}

/**
 * Run native KiCad exports using argument arrays, no shell, isolated configuration,
 * bounded buffers and 60-second timeouts per export. Throws with diagnostic receipt
 * on failure; existing output destinations are never touched.
 */
export function exportKicad({ board, schematic, output, kicad } = {}) {
  const boardSource = sourceFile(board, '.kicad_pcb', 'Board');
  const schematicSource = schematic === undefined ? null : sourceFile(schematic, '.kicad_sch', 'Schematic');
  const sources = [boardSource, ...(schematicSource ? [schematicSource] : [])];
  if (typeof output !== 'string' || !output.trim() || output.includes('\0')) fail('INVALID_NATIVE_OUTPUT', 'Provide a new output directory path.');
  const requested = path.resolve(output);
  if (exists(requested)) fail('OUTPUT_EXISTS', 'Native export requires a new output directory; existing outputs are preserved.', { path: requested });
  const explicit = typeof kicad === 'object' && kicad !== null ? kicad.bin : kicad;
  if (kicad !== undefined && (typeof explicit !== 'string' || !explicit.trim())) fail('KICAD_UNAVAILABLE', 'An explicit KiCad executable could not be selected. Supplied JSON/CSV workflows remain available.');
  const tool = findKicad(explicit);
  if (!tool.available) fail('KICAD_UNAVAILABLE', tool.reason, { tool });
  fs.mkdirSync(path.dirname(requested), { recursive: true });
  const parent = fs.realpathSync(path.dirname(requested));
  const final = path.join(parent, path.basename(requested));
  if (exists(final)) fail('OUTPUT_EXISTS', 'Native export output already exists.', { path: final });
  const staging = path.join(parent, `.pcba-native-${randomUUID()}`);
  fs.mkdirSync(staging);
  const config = path.join(staging, '.kicad-config');
  fs.mkdirSync(config);
  fs.mkdirSync(path.join(staging, 'front'));
  fs.mkdirSync(path.join(staging, 'back'));
  const positions = path.join(staging, 'positions.csv');
  const jobs = [
    { name: 'positions', args: ['pcb', 'export', 'pos', '--format', 'csv', '--units', 'mm', '--use-drill-file-origin', '--output', positions, boardSource.path] },
    { name: 'front-svg', args: ['pcb', 'export', 'svg', '--layers', 'F.Cu,F.SilkS,F.Fab,Edge.Cuts', '--mode-single', '--page-size-mode', '2', '--output', path.join(staging, 'front', 'board.svg'), boardSource.path] },
    { name: 'back-svg', args: ['pcb', 'export', 'svg', '--layers', 'B.Cu,B.SilkS,B.Fab,Edge.Cuts', '--mode-single', '--page-size-mode', '2', '--output', path.join(staging, 'back', 'board.svg'), boardSource.path] },
    ...(schematicSource ? [{ name: 'bom', args: ['sch', 'export', 'bom', '--fields', 'Reference,Value,Footprint,Datasheet', '--labels', 'Reference,Value,Footprint,Datasheet', '--output', path.join(staging, 'bom.csv'), schematicSource.path] }] : []),
  ];
  const receipt = {
    schemaVersion: 1, status: 'running', tool: { bin: tool.bin, version: tool.version }, sources, jobs: [], files: [],
    limits: { timeoutMsPerExport: TIMEOUT, outputBufferBytesPerExport: MAX_BUFFER },
    nativeConventions: { positionUnits: 'mm', origin: 'KiCad drill-file origin', rotationAndBottomSide: 'native KiCad conventions; review before canonical import', svgView: 'native KiCad layer view' },
    scope: { nativeExport: 'not_completed', canonicalNormalization: 'not_performed', designRuleCheck: 'not_run', electricalRuleCheck: 'not_run', electricalQualification: 'not_assessed', manufacturingAuthorization: 'not_assessed', sourceHashCoverage: 'declared board and optional root schematic only; hierarchical dependencies not hashed' },
  };
  let claimed = false;
  try {
    for (const job of jobs) {
      const started = new Date().toISOString();
      const result = spawnSync(tool.bin, job.args, { cwd: path.dirname(boardSource.path), encoding: 'utf8', timeout: TIMEOUT, maxBuffer: MAX_BUFFER, killSignal: 'SIGKILL', windowsHide: true, shell: false, stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, KICAD_CONFIG_HOME: config } });
      receipt.jobs.push({ name: job.name, args: job.args, startedAt: started, exitCode: result.status, signal: result.signal, stdout: result.stdout ?? '', stderr: result.stderr ?? '', ...(result.error ? { error: { code: result.error.code ?? 'SPAWN_FAILED', message: result.error.message } } : {}) });
      receipt.sources = sourceState(sources);
      if (receipt.sources.some(s => !s.unchanged)) fail('KICAD_SOURCE_CHANGED', 'A declared native source changed during export. Review the concurrent edit before retrying; no source is automatically restored.');
      if (result.error || result.status !== 0) fail(result.error?.code === 'ETIMEDOUT' ? 'KICAD_TIMEOUT' : 'KICAD_EXPORT_FAILED', `KiCad ${job.name} did not complete successfully.`, { job: job.name, exitCode: result.status, signal: result.signal, cause: result.error?.code });
    }
    receipt.files = outputFiles(staging);
    assertExports(receipt.files, Boolean(schematicSource));
    // Configuration generated in this private staging directory is not an engineering artifact.
    fs.rmSync(config, { recursive: true, force: true });
    receipt.status = 'pass'; receipt.scope.nativeExport = 'completed';
    // Own only this new directory; mkdir rejects even an empty existing destination.
    try { fs.mkdirSync(final); claimed = true; }
    catch (e) { if (e.code === 'EEXIST') fail('OUTPUT_EXISTS', 'The output appeared while KiCad was running; it was preserved.', { path: final }); throw e; }
    receipt.files = receipt.files.map(f => ({ ...f, path: `artifacts/${f.path}` }));
    fs.writeFileSync(path.join(staging, 'native-export.json'), stringify(receipt), { flag: 'wx' });
    fs.renameSync(staging, path.join(final, 'artifacts'));
    return { schemaVersion: 1, output: final, receiptPath: path.join(final, 'artifacts', 'native-export.json'), ...receipt };
  } catch (error) {
    receipt.status = 'fail'; receipt.scope.nativeExport = 'failed';
    receipt.sources = sourceState(sources);
    receipt.failure = { code: error.code ?? 'KICAD_EXPORT_FAILED', message: error.message };
    try { receipt.files = outputFiles(staging); } catch { receipt.files = []; }
    let receiptPath;
    try {
      const target = path.join(staging, 'native-export.json');
      if (!exists(target)) { fs.writeFileSync(target, stringify(receipt), { flag: 'wx' }); receiptPath = target; }
    } catch { /* A failed native process may have damaged staging; never touch source files to recover it. */ }
    if (claimed) { try { fs.rmdirSync(final); } catch { /* Remove only our still-empty reservation. */ } }
    const e = error instanceof KicadError ? error : new KicadError('KICAD_EXPORT_FAILED', error.message, { cause: error.code });
    e.details = { ...e.details, diagnosticDirectory: staging, ...(receiptPath ? { receiptPath } : {}), receipt };
    throw e;
  }
}
