import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const VERSION = '0.4.0-rc.11';
export const LINUX_VERSION = '0.4.0-rc.11-linux.1';
const pluginRoot = fileURLToPath(new URL('../', import.meta.url));
const owner = { kind: 'BOUNDARYXP_COMMUNITY_PLUGIN', schema: 1 };

export function assertSupported(platform = process.platform, arch = process.arch, version = process.versions.node,
  glibc = platform === 'linux' ? process.report.getReport().header.glibcVersionRuntime : undefined) {
  if (!['win32', 'linux'].includes(platform) || arch !== 'x64') throw new Error('BXP_PLATFORM: use Windows x64 or Linux x64 with glibc >=2.36. No installation was attempted.');
  const [major, minor] = version.split('.').map(Number);
  if (!(major > 22 || (major === 22 && minor >= 19))) throw new Error('BXP_NODE: use Node.js >=22.19 with its bundled npm.');
  if (platform === 'linux') {
    const [libMajor, libMinor] = String(glibc || '').split('.').map(Number);
    if (!(libMajor > 2 || (libMajor === 2 && libMinor >= 36))) throw new Error('BXP_LIBC: Linux requires glibc >=2.36; musl/Alpine and older glibc are unsupported. No installation was attempted.');
  }
}

export function runtimeSpec(platform = process.platform) {
  if (platform === 'win32') return { version: VERSION, directory: 'runtime' };
  if (platform === 'linux') return { version: LINUX_VERSION, directory: 'runtime/linux' };
  throw new Error('BXP_PLATFORM: no compiled package for this platform.');
}

export function resolveHome(env = process.env, platform = process.platform) {
  const defaultBase = platform === 'linux'
    ? (env.XDG_DATA_HOME || path.join(env.HOME || os.homedir(), '.local', 'share'))
    : (env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'));
  const base = env.BXP_PLUGIN_HOME || path.join(defaultBase, 'BoundaryXP', 'CommunityPlugin');
  if (!path.isAbsolute(base) || base.includes('\0') || path.resolve(base) === path.parse(path.resolve(base)).root) {
    throw new Error('BXP_HOME: choose an absolute, non-root directory dedicated to this plugin.');
  }
  return path.resolve(base);
}

export async function claimHome(home) {
  await fs.mkdir(home, { recursive: true });
  if ((await fs.lstat(home)).isSymbolicLink()) throw new Error('BXP_HOME: symlink/junction homes are not supported.');
  const marker = path.join(home, 'plugin-owner.json');
  let existing;
  try { existing = JSON.parse(await fs.readFile(marker, 'utf8')); }
  catch (error) {
    if (error.code !== 'ENOENT') throw new Error('BXP_HOME: invalid owner marker; existing content was retained.');
    if ((await fs.readdir(home)).length) throw new Error('BXP_HOME: directory is not an empty plugin home; existing content was retained.');
    try { await fs.writeFile(marker, JSON.stringify(owner), { flag: 'wx' }); }
    catch (writeError) { if (writeError.code !== 'EEXIST') throw writeError; }
    existing = JSON.parse(await fs.readFile(marker, 'utf8'));
  }
  if (existing.kind !== owner.kind || existing.schema !== owner.schema) throw new Error('BXP_HOME: directory belongs to another installation.');
}

export function safeEnvironment(env = process.env) {
  // Forward OS facilities, not provider credentials, NODE_OPTIONS or proxy secrets.
  const allowed = new Set(['path','systemroot','windir','comspec','pathext','temp','tmp','userprofile','localappdata','appdata','homedrive','homepath','systemdrive','programfiles','programfiles(x86)','programw6432','home','tmpdir','lang','lc_all']);
  return Object.fromEntries(Object.entries(env).filter(([key]) => allowed.has(key.toLowerCase())));
}

function waitForChild(child, label) {
  return new Promise((resolve, reject) => {
    child.once('error', () => reject(new Error(`${label}: could not start child process.`)));
    child.once('exit', (code, signal) => code === 0 ? resolve() : reject(new Error(`${label}: child exited (${code ?? signal}). See stderr; no automatic retry.`)));
  });
}

export async function findNpmCli(execPath = process.execPath) {
  const bin = path.dirname(await fs.realpath(execPath));
  const candidates = [path.join(bin, 'node_modules/npm/bin/npm-cli.js'),
    path.resolve(bin, '../lib/node_modules/npm/bin/npm-cli.js'),
    path.resolve(bin, '../share/nodejs/npm/bin/npm-cli.js')];
  for (const candidate of candidates) {
    try { if ((await fs.stat(candidate)).isFile()) return candidate; }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  throw new Error('BXP_NPM: install supported Node with npm beside node or in its lib/node_modules directory.');
}

export async function ensureRuntime(home) {
  const { version, directory } = runtimeSpec();
  const specDir = path.join(pluginRoot, directory);
  const lock = await fs.readFile(path.join(specDir, 'package-lock.json'));
  const lockSha256 = createHash('sha256').update(lock).digest('hex');
  const runtime = path.join(home, `runtime-${version}`);
  const receipt = path.join(runtime, 'plugin-runtime.json');
  try {
    const saved = JSON.parse(await fs.readFile(receipt, 'utf8'));
    const pkg = JSON.parse(await fs.readFile(path.join(runtime, 'node_modules/boundaryxp-mcp/package.json'), 'utf8'));
    if (saved.version !== version || saved.lock_sha256 !== lockSha256 || pkg.version !== version) throw new Error('BXP_RUNTIME: installed runtime identity differs; retain data and review installation.');
    return path.join(runtime, 'node_modules/boundaryxp-mcp/server.mjs');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    try { await fs.access(runtime); throw new Error('BXP_RUNTIME: incomplete runtime exists; it was retained for inspection.'); }
    catch (accessError) { if (accessError.code !== 'ENOENT') throw accessError; }
  }
  const npmCli = await findNpmCli();
  const lockDir = path.join(home, 'install.lock');
  try { await fs.mkdir(lockDir); }
  catch (error) { if (error.code === 'EEXIST') throw new Error('BXP_BUSY: another installation may be running. Wait for it, then reconnect. Stale locks require manual inspection.'); throw error; }
  try {
    const stage = await fs.mkdtemp(path.join(home, 'staging-'));
    await fs.copyFile(path.join(specDir, 'package.json'), path.join(stage, 'package.json'));
    await fs.writeFile(path.join(stage, 'package-lock.json'), lock);
    const userConfig = path.join(stage, 'empty-user.npmrc');
    const globalConfig = path.join(stage, 'empty-global.npmrc');
    await fs.writeFile(userConfig, '', { flag: 'wx' });
    await fs.writeFile(globalConfig, '', { flag: 'wx' });
    console.error(`BoundaryXP: installing pinned ${version} from registry.npmjs.org (no lifecycle scripts).`);
    const child = spawn(process.execPath, [npmCli, 'ci', '--prefix', stage, '--ignore-scripts', '--no-audit', '--no-fund', '--registry=https://registry.npmjs.org', '--userconfig', userConfig, '--globalconfig', globalConfig, '--cache', path.join(home, 'npm-cache')], {
      cwd: stage, env: safeEnvironment(), windowsHide: true, shell: false, stdio: ['ignore', 'pipe', 'pipe']
    });
    child.stdout.pipe(process.stderr); child.stderr.pipe(process.stderr);
    const timer = setTimeout(() => child.kill(), 240_000);
    try { await waitForChild(child, 'BXP_INSTALL'); } finally { clearTimeout(timer); }
    const pkg = JSON.parse(await fs.readFile(path.join(stage, 'node_modules/boundaryxp-mcp/package.json'), 'utf8'));
    if (pkg.version !== version) throw new Error('BXP_RUNTIME: unexpected package version.');
    await fs.writeFile(path.join(stage, 'plugin-runtime.json'), JSON.stringify({ version, lock_sha256: lockSha256 }));
    await fs.rename(stage, runtime);
    return path.join(runtime, 'node_modules/boundaryxp-mcp/server.mjs');
  } finally { await fs.rmdir(lockDir); }
}

export async function prepareData(home, server) {
  // Reuse the shipped migration contract; never rewrite old task/result bytes.
  const packageRoot = path.dirname(server);
  const pin = JSON.parse(await fs.readFile(path.join(packageRoot, 'runtime/MCP-RUNTIME-LOCK.json'), 'utf8'));
  const target = path.join(home, 'data-rc11');
  let source;
  for (const name of ['data-rc10', 'data-rc9']) {
    const candidate = path.join(home, name);
    try { await fs.lstat(candidate); source = candidate; break; }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  const lock = path.join(home, 'data-upgrade.lock');
  try { await fs.mkdir(lock); }
  catch (error) { if (error.code === 'EEXIST') throw new Error('BXP_BUSY: data preparation is running; reconnect after it completes.'); throw error; }
  try {
    try {
      const marker = JSON.parse(await fs.readFile(path.join(target, 'MCP-B332-OWNED.json'), 'utf8'));
      if ((await fs.lstat(target)).isSymbolicLink() || marker.kind !== 'MCP_B332_DATA' || marker.runtime_sha256 !== pin.manifest_sha256) throw new Error('BXP_DATA: rc.11 data identity differs; all records retained.');
      return target;
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    try {
      await fs.access(target);
      throw new Error('BXP_DATA: incomplete rc.11 directory retained for inspection.');
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (!source) return target;
    try { await fs.access(source); }
    catch (error) { if (error.code === 'ENOENT') return target; throw error; }
    const marker = JSON.parse(await fs.readFile(path.join(source, 'MCP-B332-OWNED.json'), 'utf8'));
    const { copyRuntimeData } = await import(pathToFileURL(path.join(packageRoot, 'data-migration.mjs')).href);
    copyRuntimeData({ source, destination: target, fromRuntime: marker.runtime_sha256, toRuntime: pin.manifest_sha256 });
    console.error('BoundaryXP: copied inactive plugin data to rc.11; original tasks, dictionaries and records retained.');
    return target;
  } finally { await fs.rmdir(lock); }
}

export async function main(args = process.argv.slice(2)) {
  if (args.some(arg => arg !== '--install-only') || args.length > 1) throw new Error('BXP_ARGS: only --install-only is supported. Configure BXP_PLUGIN_HOME for an isolated home.');
  assertSupported();
  const home = resolveHome();
  await claimHome(home);
  const server = await ensureRuntime(home);
  const profiles = path.join(home, 'host-profiles.json');
  try { await fs.writeFile(profiles, '{"profiles":[]}', { flag: 'wx' }); }
  catch (error) { if (error.code !== 'EEXIST') throw error; }
  // This plugin intentionally exposes host mode only. The standalone package
  // documents optional provider profiles without silently inheriting API keys.
  const profileConfig = JSON.parse(await fs.readFile(profiles, 'utf8'));
  if (!Array.isArray(profileConfig.profiles) || profileConfig.profiles.length) throw new Error('BXP_PROFILE: plugin requires an empty profiles list for host mode. Existing file was retained.');
  if (args.includes('--install-only')) { console.log(JSON.stringify({ version: runtimeSpec().version, ready: true })); return; }
  const data = await prepareData(home, server);
  const child = spawn(process.execPath, [server, '--data-root', data, '--profiles', profiles], {
    cwd: home, env: safeEnvironment(), windowsHide: true, shell: false, stdio: 'inherit'
  });
  const stop = () => child.kill();
  process.once('SIGINT', stop); process.once('SIGTERM', stop);
  try { await waitForChild(child, 'BXP_MCP'); }
  finally { process.off('SIGINT', stop); process.off('SIGTERM', stop); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
