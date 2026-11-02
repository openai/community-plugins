import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { cpSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { inspectPackage, json, packageRoot, privateDirectory, repo, testEnvironment } from '../support/package.mjs';

test('the actual marketplace validator accepts a cold copied package and rejects a broken native handoff', t => {
  const root = privateDirectory(t);
  mkdirSync(path.join(root, 'scripts')); mkdirSync(path.join(root, '.agents/plugins'), { recursive: true });
  const plugin = path.join(root, 'plugins/robot-skill-hub'); cpSync(packageRoot, plugin, { recursive: true });
  cpSync(path.join(repo, 'scripts/validate-marketplace.mjs'), path.join(root, 'scripts/validate-marketplace.mjs'));
  const catalog = json(path.join(repo, '.agents/plugins/marketplace.json'));
  catalog.plugins = catalog.plugins.filter(entry => entry.name === 'robot-skill-hub'); assert.equal(catalog.plugins.length, 1);
  writeFileSync(path.join(root, '.agents/plugins/marketplace.json'), JSON.stringify(catalog));
  inspectPackage(plugin);
  const run = () => spawnSync(process.execPath, [path.join(root, 'scripts/validate-marketplace.mjs')], { cwd: root, env: testEnvironment(), encoding: 'utf8', timeout: 10000, maxBuffer: 262144, windowsHide: true });
  let result = run(); assert.equal(result.error, undefined); assert.equal(result.status, 0, result.stderr);
  const filename = path.join(plugin, '.codex-plugin/plugin.json'); const native = json(filename); native.mcpServers = './missing.json'; writeFileSync(filename, JSON.stringify(native));
  result = run(); assert.equal(result.status, 1); assert.match(result.stderr, /manifest mcpServers path does not exist/);
});

test('actual catalog dispatch selects the new canonical master (fixture executor; does not run other plugins)', t => {
  const root = privateDirectory(t);
  mkdirSync(path.join(root, 'scripts')); mkdirSync(path.join(root, '.agents/plugins'), { recursive: true });
  cpSync(path.join(repo, 'scripts/run-marketplace-tests.mjs'), path.join(root, 'scripts/run-marketplace-tests.mjs'));
  const catalog = json(path.join(repo, '.agents/plugins/marketplace.json')); const scripts = json(path.join(repo, 'package.json')).scripts;
  assert.match(scripts['test:robot-skill-hub'], /tests\/robot-skill-hub\/integration\/\*\.test\.mjs/);
  writeFileSync(path.join(root, '.agents/plugins/marketplace.json'), JSON.stringify(catalog));
  writeFileSync(path.join(root, 'package.json'), JSON.stringify({ type: 'module', scripts }));
  const cli = path.join(root, 'npm-cli.js');
  writeFileSync(cli, "import {appendFileSync} from 'node:fs'; appendFileSync('called.jsonl',JSON.stringify(process.argv.slice(2))+'\\n');\n");
  const result = spawnSync(process.execPath, [path.join(root, 'scripts/run-marketplace-tests.mjs')], { cwd: root, env: testEnvironment({ npm_execpath: cli, npm_lifecycle_event: 'test:marketplace' }), encoding: 'utf8', timeout: 10000, maxBuffer: 262144, windowsHide: true });
  assert.equal(result.error, undefined); assert.equal(result.status, 0, result.stderr);
  const calls = readFileSync(path.join(root, 'called.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line));
  assert.deepEqual(calls, catalog.plugins.map(entry => ['run', `test:${entry.name}`]));
  assert.equal(calls.filter(call => call[1] === 'test:robot-skill-hub').length, 1);
});
