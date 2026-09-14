#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// One root master; native effects require explicit, validated simulator prerequisites.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, realpathSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const ensure = (condition, message) => { if (!condition) throw new Error(message); };
function enabled(env, key) {
  ensure(env[key] === undefined || ['0', '1'].includes(env[key]), `${key} must be 0 or 1`);
  return env[key] === '1';
}
function directory(value, label) {
  ensure(typeof value === 'string' && path.isAbsolute(value), `${label} requires an absolute directory`);
  ensure(statSync(value).isDirectory(), `${label} is not a directory`);
  return realpathSync(value);
}
export function planMaster({ root = ROOT, env = process.env, node = process.execPath, platform = process.platform } = {}) {
  ensure(env.npm_lifecycle_event === 'test:openpnp', 'Run npm run test:openpnp from the repository root');
  ensure(typeof env.npm_execpath === 'string' && path.isAbsolute(env.npm_execpath), 'Absolute npm lifecycle entrypoint required');
  const npm = realpathSync(env.npm_execpath);
  ensure(path.basename(npm) === 'npm-cli.js' && statSync(npm).isFile(), 'Regular npm-cli.js required');
  const native = enabled(env, 'OPENPNP_TEST_NATIVE'), gui = enabled(env, 'OPENPNP_TEST_GUI');
  const phases = [{ id: 'contracts', command: node, args: [npm, 'run', 'test:openpnp:contracts'] }];
  const skipped = [];
  let output, source, containerNode;
  if (native || gui) {
    const requested = env.OPENPNP_TEST_OUTPUT || `validation/master-${randomUUID()}`;
    output = path.resolve(root, requested);
    ensure(output.startsWith(path.join(root, 'validation') + path.sep), 'Output must be a new descendant of repository validation/');
    ensure(!existsSync(output), 'Qualification output already exists; retain it and choose a new directory');
    // A symlinked existing ancestor must not redirect qualification outside the checkout.
    let ancestor = path.dirname(output);
    while (!existsSync(ancestor)) ancestor = path.dirname(ancestor);
    ensure(realpathSync(ancestor) === ancestor, 'Symbolic qualification output ancestor refused');
  }
  if (native) {
    source = directory(env.OPENPNP_SOURCE, 'OPENPNP_SOURCE');
    ensure(existsSync(path.join(source, 'pom.xml')) && existsSync(path.join(source, '.git')), 'Pinned Git OpenPnP source checkout required');
    ensure(platform === 'linux' || env.OPENPNP_TEST_CONTAINER_NODE_ROOT, 'Non-Linux hosts must explicitly supply a Linux Node22.19 runtime with OPENPNP_TEST_CONTAINER_NODE_ROOT');
    containerNode = directory(env.OPENPNP_TEST_CONTAINER_NODE_ROOT || path.dirname(path.dirname(realpathSync(node))), 'Container Node root');
    ensure(statSync(path.join(containerNode, 'bin/node')).isFile(), 'Container Node executable missing');
    const relative = path.relative(root, output);
    for (const id of ['native', 'packaged-mcp-controller-history', 'jvm-halts']) {
      phases.push({ id, command: 'bash', args: ['scripts/openpnp-qualify-native.sh', id, relative],
        env: { OPENPNP_SOURCE: source, OPENPNP_TEST_CONTAINER_NODE_ROOT: containerNode } });
    }
  } else skipped.push({ id: 'native,packaged-mcp-controller-history,jvm-halts', reason: 'Set OPENPNP_TEST_NATIVE=1 with pinned OPENPNP_SOURCE, Docker and compatible Linux Node22.19 runtime. No native result inferred.' });
  if (gui) {
    const build = directory(env.OPENPNP_TEST_GUI_BUILD, 'OPENPNP_TEST_GUI_BUILD');
    const javaHome = directory(env.OPENPNP_TEST_JAVA_HOME, 'OPENPNP_TEST_JAVA_HOME');
    ensure(build.startsWith(root + path.sep) && existsSync(path.join(build, 'runtime/codex-build-manifest.json')) && existsSync(path.join(build, 'test-classes')), 'Explicit compatible immutable GUI build below this checkout required');
    ensure(existsSync(path.join(javaHome, 'bin/java')), 'GUI Java executable missing');
    ensure(platform === 'darwin' || (platform === 'linux' && (env.DISPLAY || env.WAYLAND_DISPLAY)), 'Native GUI requires an explicit desktop display');
    for (const name of ['ownership', 'unknown-exit', 'topology', 'panel-membership', 'panel-takeover', 'title', 'board-inspection']) {
      phases.push({ id: `gui-${name}`, command: 'python3', args: ['scripts/openpnp-test-native-gui.py', '--build', build, '--java-home', javaHome, '--run', path.join(output, `gui-${name}`), '--node', node, ...(name === 'ownership' ? [] : [`--${name}`])] });
    }
  } else skipped.push({ id: 'native-gui', reason: 'Set OPENPNP_TEST_GUI=1 with OPENPNP_TEST_GUI_BUILD, OPENPNP_TEST_JAVA_HOME and a compatible native display. Scripted GUI is distinct from manual desktop qualification.' });
  skipped.push({ id: 'physical,manual-desktop,performance,endurance', reason: 'Separate explicit qualification protocols and environments; the master never infers these from bounded tests. See tests/openpnp/README.md.' });
  return { phases, skipped, output };
}
export function reserveOutput(output) {
  mkdirSync(path.dirname(output), { recursive: true, mode: 0o700 });
  ensure(realpathSync(path.dirname(output)) === path.dirname(output), 'Symbolic qualification output ancestor refused');
  // Exclusive final mkdir preserves another concurrent run's evidence.
  mkdirSync(output, { mode: 0o700 });
}
export function runPlan(plan, { root = ROOT, env = process.env, run = spawnSync, log = console.log } = {}) {
  for (const row of plan.skipped) log(`SKIP ${row.id}: ${row.reason}`);
  for (const phase of plan.phases) {
    log(`RUN ${phase.id}`);
    const result = run(phase.command, phase.args, { cwd: root, env: { ...env, ...phase.env }, stdio: 'inherit', shell: false });
    if (result.error || result.signal || result.status !== 0) {
      log(`FAIL ${phase.id}: ${result.error?.message || result.signal || result.status}`);
      return Number.isInteger(result.status) && result.status > 0 ? result.status : 1;
    }
    log(`PASS ${phase.id}`);
  }
  return 0;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const plan = planMaster();
    if (plan.output) reserveOutput(plan.output);
    process.exitCode = runPlan(plan);
  } catch (error) { console.error(`OpenPnP master refused: ${error.message}`); process.exitCode = 1; }
}
