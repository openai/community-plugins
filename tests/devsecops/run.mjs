#!/usr/bin/env node
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { repo, plugin, files } from './support/package.mjs';
import { requireUvVersion } from './support/uv.mjs';

const suite = path.dirname(fileURLToPath(import.meta.url));
const privateRoot = path.join(os.homedir(), '.cache/devsecops');
const cache = path.resolve(process.env.DEVSECOPS_TEST_CACHE || path.join(privateRoot, 'community-tests'));
if (!cache.startsWith(privateRoot + path.sep)) throw new Error('DEVSECOPS_TEST_CACHE must be beneath ~/.cache/devsecops');
if (cache === plugin || cache.startsWith(plugin + path.sep)) throw new Error('Test cache must stay outside the plugin');
for (let item = cache; item !== path.dirname(item); item = path.dirname(item))
  if (fs.existsSync(item) && fs.lstatSync(item).isSymbolicLink()) throw new Error('Test cache cannot use symlink paths');
if (!['linux', 'darwin'].includes(process.platform)) throw new Error('Full executable suite requires Linux/macOS; Windows coverage is not claimed');
if (process.platform !== 'darwin' && process.env.DEVSECOPS_TEST_NETWORK_SANDBOX === '1')
  throw new Error('DEVSECOPS_TEST_NETWORK_SANDBOX is an explicit macOS-only opt-in; never enable it on Linux');
fs.mkdirSync(path.join(cache, 'results'), { recursive: true, mode: 0o700 });
const output = fs.mkdtempSync(path.join(cache, 'results', 'run-'));
const context = path.join(output, 'context.json');
const env = { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTEST_DISABLE_PLUGIN_AUTOLOAD: '1', UV_PYTHON_DOWNLOADS: 'never',
  UV_PROJECT_ENVIRONMENT: path.join(cache, 'venv'), UV_CACHE_DIR: path.join(cache, 'uv'),
  GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: os.devNull, GIT_TERMINAL_PROMPT: '0', GIT_OPTIONAL_LOCKS: '0' };
delete env.PYTEST_ADDOPTS;
delete env.PYTHONPATH;
delete env.PYTHONHOME;
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const snapshot = () => Object.fromEntries(files(plugin).map(file => [path.relative(plugin, file), hash(fs.readFileSync(file))]));
const before = snapshot();
const steps = [];
let active;
let interrupted;
let interruptKillTimer;
function signal(child, name) {
  if (!child?.pid) return;
  try { process.kill(-child.pid, name); } catch (error) { if (error.code !== 'ESRCH') throw error; }
}
function run(command, args, timeout = 15 * 60 * 1000, { capture = false } = {}) {
  if (interrupted) return Promise.reject(new Error(`Master interrupted by ${interrupted}`));
  console.log(`\n> ${path.basename(command)} ${args.join(' ')}`);
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const child = spawn(command, args, { cwd: repo, env, stdio: capture ? ['ignore', 'pipe', 'inherit'] : 'inherit', detached: true });
    active = child;
    let captured = '';
    child.stdout?.on('data', bytes => {
      captured = (captured + bytes.toString('utf8')).slice(0, 4097);
      if (captured.length > 4096) signal(child, 'SIGKILL');
    });
    let expired = false;
    let forced;
    const timer = setTimeout(() => {
      expired = true;
      signal(child, 'SIGTERM');
      forced = setTimeout(() => signal(child, 'SIGKILL'), 5000);
    }, timeout);
    child.on('error', error => { clearTimeout(timer); clearTimeout(forced); active = undefined; reject(error); });
    child.on('close', (code, exitSignal) => {
      clearTimeout(timer);
      clearTimeout(forced);
      // The captured child still identifies its group after the leader exits.
      if (expired || interrupted) signal(child, 'SIGKILL');
      if (interrupted) clearTimeout(interruptKillTimer);
      active = undefined;
      steps.push({ command: path.basename(command), exit_code: code, signal: exitSignal, timed_out: expired, duration_ms: Date.now() - started });
      if (code !== 0 || expired || interrupted) reject(new Error(`${path.basename(command)} failed: exit=${code}, signal=${exitSignal}, timeout=${expired}, interrupted=${interrupted || 'no'}`));
      else resolve(captured);
    });
  });
}
for (const name of ['SIGINT', 'SIGTERM']) process.once(name, () => {
  interrupted = name;
  const ownedChild = active;
  signal(ownedChild, 'SIGTERM');
  // Capture the group now: close clears `active`, but descendants can outlive it.
  interruptKillTimer = setTimeout(() => signal(ownedChild, 'SIGKILL'), 5000);
});
let failure;
try {
  await run(process.execPath, [path.join(repo, 'scripts/validate-marketplace.mjs')], 60000);
  await run(process.execPath, [path.join(suite, 'contracts.mjs'), '--static'], 60000);
  const uv = process.env.DEVSECOPS_TEST_UV || 'uv';
  const python = process.env.DEVSECOPS_TEST_BOOTSTRAP_PYTHON || 'python3';
  await run(python, ['-I', '-B', '-c', 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"']);
  await run('git', ['--version'], 30000);
  requireUvVersion(await run(uv, ['--version'], 30000, { capture: true }));
  await run(uv, ['sync', '--project', plugin, '--frozen', '--no-install-project', '--python', python,
    ...(process.env.DEVSECOPS_TEST_OFFLINE === '1' ? ['--offline'] : [])]);
  const preparedPython = path.join(cache, 'venv/bin/python');
  await run(preparedPython, ['-I', '-B', path.join(suite, 'support/prepare_tools.py'), plugin, cache, context], 20 * 60 * 1000);
  const prepared = JSON.parse(fs.readFileSync(context, 'utf8'));
  Object.assign(env, prepared.environment, { DEVSECOPS_TEST_CONTEXT: context });
  fs.writeFileSync(path.join(cache, 'latest-context.json'), fs.readFileSync(context), { mode: 0o600 });
  // This call discovers the whole packaged Python suite; there is no -k/-m selection.
  await run(preparedPython, ['-I', '-B', path.join(suite, 'support/run_python.py'), plugin, output], 75 * 60 * 1000);
  await run(process.execPath, [path.join(suite, 'contracts.mjs')], 10 * 60 * 1000);
} catch (error) {
  failure = error.message;
} finally {
  let unchanged = false;
  try { unchanged = JSON.stringify(snapshot()) === JSON.stringify(before); }
  catch (error) { failure ||= error.message; }
  if (!unchanged) failure ||= 'The plugin source changed during the master suite';
  fs.writeFileSync(path.join(output, 'master.json'), JSON.stringify({
    scope: 'community-package-local-automated-suite', completed: !failure,
    failure: failure || null, interrupted: interrupted || null,
    source_unchanged: unchanged, source_sha256: before, steps,
    live_gitlab_cluster_or_video_qualification: false,
  }, null, 2) + '\n', { mode: 0o600 });
  console.log(`DevSecOps receipts: ${output}`);
}
if (failure) { console.error(`ERROR ${failure}`); process.exitCode = 1; }
else console.log('DevSecOps local automated master completed. Read python-suite.json for every coverage gap. This is not live factory or video qualification.');
