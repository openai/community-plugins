// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm, symlink, readFile, realpath, chmod } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { planMaster, runPlan, reserveOutput } from '../../scripts/test-openpnp.mjs';
const root = fileURLToPath(new URL('../../', import.meta.url));
async function fixture(t) {
  const dir = await realpath(await mkdtemp(path.join(os.tmpdir(), 'openpnp-master-')));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const npm = path.join(dir, 'npm-cli.js'), source = path.join(dir, 'upstream'), nodeRoot = path.join(dir, 'linux node');
  await writeFile(npm, 'process.exit(0)');
  await mkdir(path.join(source, '.git'), { recursive: true }); await writeFile(path.join(source, 'pom.xml'), '<project/>');
  await mkdir(path.join(nodeRoot, 'bin'), { recursive: true }); await writeFile(path.join(nodeRoot, 'bin/node'), 'explicit test prerequisite');
  return { dir, npm, source, nodeRoot, env: { npm_lifecycle_event: 'test:openpnp', npm_execpath: npm }, opts: { root: dir, node: process.execPath, platform: 'linux' } };
}
test('default master includes all contract checks and explicitly skips native prerequisites', async t => {
  const f = await fixture(t), p = planMaster({ ...f.opts, env: f.env });
  assert.deepEqual(p.phases.map(x => x.id), ['contracts']);
  assert.deepEqual(p.phases[0].args, [f.npm, 'run', 'test:openpnp:contracts']);
  assert.equal(p.output, undefined); assert.equal(p.skipped.length, 3);
  assert.match(p.skipped[0].id, /native.*controller-history.*jvm-halts/);
});
test('native prerequisite gate dispatches build then packaged MCP/controller/history then actual halts', async t => {
  const f = await fixture(t), env = { ...f.env, OPENPNP_TEST_NATIVE: '1', OPENPNP_SOURCE: f.source, OPENPNP_TEST_CONTAINER_NODE_ROOT: f.nodeRoot, OPENPNP_TEST_OUTPUT: 'validation/a space;literal' };
  const p = planMaster({ ...f.opts, env });
  assert.deepEqual(p.phases.map(x => x.id), ['contracts', 'native', 'packaged-mcp-controller-history', 'jvm-halts']);
  for (const phase of p.phases.slice(1)) { assert.equal(phase.command, 'bash'); assert.equal(phase.args[2], 'validation/a space;literal'); assert.equal(phase.env.OPENPNP_SOURCE, f.source); }
  assert.ok(!p.skipped.some(x => x.id.includes('jvm-halts')));
});
test('explicit bad or missing native prerequisites refuse before any phase can run', async t => {
  const f = await fixture(t);
  for (const patch of [{ OPENPNP_TEST_NATIVE: 'yes' }, { OPENPNP_TEST_NATIVE: '1' }, { OPENPNP_TEST_GUI: '1' }])
    assert.throws(() => planMaster({ ...f.opts, env: { ...f.env, ...patch } }));
  assert.throws(() => planMaster({ ...f.opts, platform: 'darwin', env: { ...f.env, OPENPNP_TEST_NATIVE: '1', OPENPNP_SOURCE: f.source } }), /Linux Node/);
});
test('qualification refuses reused, escaped and symlinked output directories', async t => {
  const f = await fixture(t), env = { ...f.env, OPENPNP_TEST_NATIVE: '1', OPENPNP_SOURCE: f.source, OPENPNP_TEST_CONTAINER_NODE_ROOT: f.nodeRoot };
  await mkdir(path.join(f.dir, 'validation/used'), { recursive: true }); await symlink(os.tmpdir(), path.join(f.dir, 'validation/link'));
  for (const value of ['validation/used', '../escape', 'validation/link/new']) assert.throws(() => planMaster({ ...f.opts, env: { ...env, OPENPNP_TEST_OUTPUT: value } }));
  const planned = planMaster({ ...f.opts, env: { ...env, OPENPNP_TEST_OUTPUT: 'validation/reserved' } });
  reserveOutput(planned.output);
  assert.throws(() => reserveOutput(planned.output), { code: 'EEXIST' });
});
test('GUI gate requires explicit compatible immutable build and dispatches seven distinct cases', async t => {
  const f = await fixture(t), build = path.join(f.dir, 'immutable build'), java = path.join(f.dir, 'jdk');
  await mkdir(path.join(build, 'runtime'), { recursive: true }); await mkdir(path.join(build, 'test-classes')); await writeFile(path.join(build, 'runtime/codex-build-manifest.json'), '{}');
  await mkdir(path.join(java, 'bin'), { recursive: true }); await writeFile(path.join(java, 'bin/java'), 'test prerequisite');
  const env = { ...f.env, OPENPNP_TEST_GUI: '1', OPENPNP_TEST_GUI_BUILD: build, OPENPNP_TEST_JAVA_HOME: java };
  assert.throws(() => planMaster({ ...f.opts, env }), /display/);
  const p = planMaster({ ...f.opts, platform: 'darwin', env });
  assert.equal(p.phases.length, 8); assert.equal(new Set(p.phases.map(x => x.id)).size, 8);
  assert.ok(p.phases.at(-1).args.includes('--board-inspection')); assert.ok(p.phases.at(-1).args.includes(process.execPath));
});
test('dispatcher forwards arguments without a shell and stops at first failed phase', async t => {
  const f = await fixture(t); const calls = [], logs = [], plan = { skipped: [{ id: 'hardware', reason: 'not qualified' }], phases: ['one', 'two', 'three'].map(id => ({ id, command: '/exact runtime', args: [id, 'a;literal'], env: { SELECTED: id } })) };
  const code = runPlan(plan, { root: f.dir, env: { INHERITED: 'yes' }, log: x => logs.push(x), run: (...args) => { calls.push(args); return { status: calls.length === 2 ? 7 : 0 }; } });
  assert.equal(code, 7); assert.equal(calls.length, 2); assert.equal(calls[0][2].shell, false); assert.equal(calls[1][2].env.SELECTED, 'two'); assert.equal(calls[0][1][1], 'a;literal'); assert.match(logs[0], /^SKIP/); assert.ok(!logs.includes('PASS two'));
});
test('spawn failures and signals fail without dispatching a later phase', () => {
  for (const result of [{ error: new Error('spawn refused') }, { status: null, signal: 'SIGTERM' }]) {
    let calls = 0; const code = runPlan({ skipped: [], phases: [{ id: 'first', command: 'x', args: [] }, { id: 'next', command: 'x', args: [] }] }, { log: () => {}, run: () => { calls++; return result; } });
    assert.equal(code, 1); assert.equal(calls, 1);
  }
});
test('master requires valid npm lifecycle and never falls back to PATH npm', async t => {
  const f = await fixture(t);
  for (const env of [{}, { ...f.env, npm_lifecycle_event: 'other' }, { ...f.env, npm_execpath: 'npm' }]) assert.throws(() => planMaster({ ...f.opts, env }));
});
test('actual master process invokes exact inherited npm CLI with contracts and no native dispatch by default', async t => {
  const f = await fixture(t), receipt = path.join(f.dir, 'invoked.json');
  await writeFile(f.npm, `require('node:fs').writeFileSync(${JSON.stringify(receipt)}, JSON.stringify({args:process.argv.slice(2),cwd:process.cwd()}));`);
  const env = { ...process.env, ...f.env }; for (const key of Object.keys(env)) if (key.startsWith('OPENPNP_TEST_')) delete env[key];
  const result = spawnSync(process.execPath, [path.join(root, 'scripts/test-openpnp.mjs')], { env, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr); assert.deepEqual(JSON.parse(await readFile(receipt)).args, ['run', 'test:openpnp:contracts']); assert.match(result.stdout, /SKIP native,packaged-mcp-controller-history,jvm-halts/);
});
test('workflow native qualification uses the same root master rather than private workflow-only phases', async () => {
  const workflow = await readFile(path.join(root, '.github/workflows/openpnp.yml'), 'utf8');
  assert.match(workflow, /OPENPNP_TEST_NATIVE: "1"/); assert.match(workflow, /npm run test:openpnp 2>&1/);
  assert.doesNotMatch(workflow, /run:.*scripts\/openpnp-test-native|docker run --rm/);
});

test('actual shell phase adapter passes fixed native paths and container environment without running a native process', async t => {
  const f = await fixture(t), bin = path.join(f.dir, 'fake-bin'), receipt = path.join(f.dir, 'docker.json');
  await mkdir(bin); const docker = path.join(bin, 'docker');
  await writeFile(docker, `#!/bin/sh\nexec "${process.execPath}" "${path.join(f.dir, 'capture.cjs')}" "$@"\n`);
  await chmod(docker, 0o700); await writeFile(path.join(f.dir, 'capture.cjs'), `require('node:fs').writeFileSync(${JSON.stringify(receipt)},JSON.stringify(process.argv.slice(2)));`);
  const env = { ...process.env, PATH: bin + path.delimiter + process.env.PATH, OPENPNP_SOURCE: f.source, OPENPNP_MAVEN_CACHE: path.join(f.dir, 'm2'), OPENPNP_TEST_CONTAINER_NODE_ROOT: f.nodeRoot };
  delete env.OPENPNP_RUNTIME; delete env.OPENPNP_BUILD_DIR;
  for (const phase of ['native', 'packaged-mcp-controller-history', 'jvm-halts']) {
    const result = spawnSync('bash', ['scripts/openpnp-qualify-native.sh', phase, 'validation/test space;literal'], { cwd: root, env, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr); const args = JSON.parse(await readFile(receipt));
    assert.ok(args.includes('maven:3.9.9-eclipse-temurin-17@sha256:f58d59b6273e785ac0a4477f6e9b5ba1d7731c75b906c0f7b34076f1851318cc'));
    if (phase === 'native') assert.equal(args.at(-1), 'validation/test space;literal/ci-native');
    else assert.ok(args.includes('OPENPNP_QUALIFICATION_ROOT=/workspace/validation/test space;literal'));
  }
  assert.notEqual(spawnSync('bash', ['scripts/openpnp-qualify-native.sh', 'unknown', 'validation/new'], { cwd: root, env }).status, 0);
});
