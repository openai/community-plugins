#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Rebuilds only in a private temporary tree. Does not install dependencies or rewrite source/package files.
import assert from 'node:assert/strict';
import { verifyControllerHistoryPackage } from './verify-openpnp-controller-history.mjs';
import { verifyNativePatchPackages } from './verify-openpnp-native-patches.mjs';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cp, mkdir, mkdtemp, readFile, readdir, rm, lstat } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const repository = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

function requireNode() {
  const [major, minor] = process.versions.node.split('.').map(Number);
  assert.ok(major > 22 || (major === 22 && minor >= 19), 'Use Node.js 22.19.0 or newer to verify this package.');
}

async function runBuild(file, cwd) {
  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [file], { cwd, stdio: ['ignore', 'pipe', 'pipe'], shell: false });
    let output = '';
    const capture = chunk => { output = (output + chunk.toString('utf8')).slice(-8000); };
    child.stdout.on('data', capture); child.stderr.on('data', capture);
    const timer = setTimeout(() => child.kill('SIGKILL'), 30_000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', (code, signal) => {
      clearTimeout(timer);
      if (code === 0) resolve();
      else reject(new Error(`Temporary ${file} failed (${signal ?? code}): ${output.trim()}`));
    });
  });
}

async function bytesEqual(expectedFile, actualFile, label) {
  const [expected, actual] = await Promise.all([readFile(expectedFile), readFile(actualFile)]);
  assert.equal(digest(actual), digest(expected), `${label} differs from the current source build. Rebuild the package before release.`);
  return { path: label, bytes: actual.length, sha256: digest(actual) };
}

function dependencyRoots(inputs) {
  return [...new Set(inputs.filter(file => file.startsWith('node_modules/')).map(file => {
    const parts = file.split('/');
    return parts[1].startsWith('@') ? parts.slice(0, 3).join('/') : parts.slice(0, 2).join('/');
  }))].sort();
}

async function verifyToolDiscovery(source, plugin, temporary) {
  const { TOOL_DEFINITIONS, publicDefinition } = await import(pathToFileURL(path.join(source, 'contracts.mjs')).href);
  const { Client } = await import(pathToFileURL(path.join(source, 'node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js')).href);
  const { StdioClientTransport } = await import(pathToFileURL(path.join(source, 'node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js')).href);
  const state = path.join(temporary, 'offline-client-state');
  await mkdir(state);
  const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(plugin, 'mcp/server.mjs'), '--stdio'], cwd: state,
    env: { PATH: process.env.PATH ?? '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: path.join(state, 'intentionally-absent-connection.json') }, stderr: 'pipe' });
  const client = new Client({ name: 'openpnp-package-verifier', version: '1.0.0' });
  let stderr = '';
  transport.stderr?.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-4000); });
  try {
    await client.connect(transport);
    const { tools } = await client.listTools(undefined, { timeout: 10_000 });
    const byName = (a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
    assert.deepEqual([...tools].sort(byName), TOOL_DEFINITIONS.map(publicDefinition).sort(byName),
      'Packaged MCP discovery differs from contracts.mjs public tool schemas/descriptions/annotations.');
    const capabilities = await client.callTool({ name: 'openpnp_get_capabilities', arguments: {} }, undefined, { timeout: 10_000 });
    assert.equal(capabilities.structuredContent?.connected, false, 'Verification must stay offline.');
    assert.equal(capabilities.structuredContent?.machine_state, 'unknown');
    return tools.length;
  } catch (error) {
    throw new Error(`Packaged MCP contract verification failed: ${error.message}${stderr ? `\n${stderr}` : ''}`, { cause: error });
  } finally { await client.close(); }
}

export async function verifyPackageArtifactBindings(pluginRoot) {
  const names = ['bridge/build-manifest.json', 'controller-history/build-manifest.json', 'assets/evaluations/job-edit-tool-contracts.json'];
  const [bridge, helper, evidence] = await Promise.all(names.map(async name => {
    const file = path.join(pluginRoot, name), info = await lstat(file);
    assert.ok(info.isFile() && !info.isSymbolicLink() && info.size <= 1024 * 1024, `Invalid package metadata: ${name}`);
    return JSON.parse(await readFile(file, 'utf8'));
  }));
  const current = evidence.candidate_packaged_mcp;
  assert.ok(current && typeof current === 'object', 'Current package metadata is missing.');
  for (const hash of [bridge.bridge_sha256, bridge.runtime_manifest_sha256, helper.built_against_bridge_sha256,
    helper.runtime_manifest_sha256, current.native_bridge_sha256, current.native_runtime_manifest_sha256])
    assert.match(hash, /^[a-f0-9]{64}$/, 'Package identities must be SHA-256 hashes.');
  assert.equal(helper.built_against_bridge_sha256, bridge.bridge_sha256, 'History helper must bind the selected Bridge.');
  assert.equal(current.native_bridge_sha256, bridge.bridge_sha256, 'Current metadata must bind the selected Bridge.');
  assert.equal(helper.runtime_manifest_sha256, bridge.runtime_manifest_sha256, 'History helper must bind the selected runtime manifest.');
  assert.equal(current.native_runtime_manifest_sha256, bridge.runtime_manifest_sha256, 'Current metadata must bind the selected runtime manifest.');
  return { bridge_sha256: bridge.bridge_sha256, runtime_manifest_sha256: bridge.runtime_manifest_sha256,
    scope: 'Package metadata binding only; actual native runtime and execution require separate qualification.' };
}

export async function verifyOpenPnpPackage({ repositoryRoot = repository, pluginRoot = path.join(repositoryRoot, 'plugins/openpnp') } = {}) {
  requireNode();
  const artifactBindings = await verifyPackageArtifactBindings(pluginRoot);
  const nativePatches = await verifyNativePatchPackages({ pluginRoot });
  const source = path.join(repositoryRoot, 'src/openpnp/node');
  const sourceNames = (await readdir(source)).filter(name => name.endsWith('.mjs') || ['package.json', 'package-lock.json'].includes(name)).sort();
  const originalSources = new Map();
  for (const name of sourceNames) {
    assert.ok((await lstat(path.join(source, name))).isFile(), `Non-regular source input: ${name}`);
    originalSources.set(name, digest(await readFile(path.join(source, name))));
  }
  const sharedProfile = path.join(repositoryRoot, 'plugins/openpnp/scripts/controller-profile.mjs');
  assert.ok((await lstat(sharedProfile)).isFile(), 'The shared controller profile must be a regular source file.');
  const sharedProfileHash = digest(await readFile(sharedProfile));
  const temporary = await mkdtemp(path.join(os.tmpdir(), 'openpnp-package-verification-'));
  try {
    const temporarySource = path.join(temporary, 'src/openpnp/node');
    const temporaryPlugin = path.join(temporary, 'plugins/openpnp');
    await mkdir(temporarySource, { recursive: true });
    for (const name of sourceNames) await cp(path.join(source, name), path.join(temporarySource, name));
    await mkdir(path.join(temporaryPlugin, 'scripts'), { recursive: true });
    await cp(sharedProfile, path.join(temporaryPlugin, 'scripts/controller-profile.mjs'));
    try {
      // Copy installed dependencies so esbuild resolves identical relative module paths in the temporary tree.
      await cp(path.join(source, 'node_modules'), path.join(temporarySource, 'node_modules'), { recursive: true, verbatimSymlinks: true });
    } catch (error) { throw new Error('Prepared source dependencies are required. Run the repository build first; this verifier never installs packages.', { cause: error }); }
    await runBuild('build.mjs', temporarySource);
    await runBuild('notices.mjs', temporarySource);

    const inputs = JSON.parse(await readFile(path.join(temporarySource, 'build-inputs.json'), 'utf8'));
    assert.ok(inputs.includes('contracts.mjs'), 'The public contracts source must be part of the generated bundle.');
    assert.ok(inputs.includes('../../../plugins/openpnp/scripts/controller-profile.mjs'), 'The shared profile source must be inventoried in the bundle.');
    const lock = JSON.parse(await readFile(path.join(source, 'package-lock.json'), 'utf8'));
    const sourcePackage = JSON.parse(await readFile(path.join(source, 'package.json'), 'utf8'));
    assert.deepEqual(lock.packages[''].dependencies, sourcePackage.dependencies, 'package.json dependencies differ from lockfile.');
    assert.deepEqual(lock.packages[''].devDependencies, sourcePackage.devDependencies, 'Build dependencies differ from lockfile.');
    const roots = [...new Set([...dependencyRoots(inputs), 'node_modules/esbuild'])];
    for (const root of roots) {
      const installed = JSON.parse(await readFile(path.join(temporarySource, root, 'package.json'), 'utf8'));
      assert.equal(installed.version, lock.packages[root]?.version, `Installed dependency version differs from lockfile: ${root}`);
    }

    const artifacts = [];
    const generatedNames = (await readdir(path.join(temporaryPlugin, 'mcp'), { withFileTypes: true }))
      .filter(entry => entry.isFile()).map(entry => entry.name).sort();
    const packagedNames = (await readdir(path.join(pluginRoot, 'mcp'), { withFileTypes: true }))
      .filter(entry => !entry.isDirectory()).map(entry => entry.name).sort();
    assert.deepEqual(packagedNames, generatedNames, 'Packaged MCP root has missing or unexpected generated artifacts.');
    for (const name of generatedNames) artifacts.push(await bytesEqual(path.join(temporaryPlugin, 'mcp', name), path.join(pluginRoot, 'mcp', name), `mcp/${name}`));
    artifacts.push(await bytesEqual(path.join(temporaryPlugin, 'THIRD_PARTY_NOTICES.md'), path.join(pluginRoot, 'THIRD_PARTY_NOTICES.md'), 'THIRD_PARTY_NOTICES.md'));
    await bytesEqual(path.join(temporarySource, 'build-inputs.json'), path.join(source, 'build-inputs.json'), 'src/openpnp/node/build-inputs.json');
    const toolCount = await verifyToolDiscovery(source, pluginRoot, temporary);
    for (const [name, before] of originalSources) assert.equal(digest(await readFile(path.join(source, name))), before,
      `Source ${name} changed during verification; rerun against a stable source tree.`);
    assert.equal(digest(await readFile(sharedProfile)), sharedProfileHash, 'Shared controller profile changed during verification.');
    const controllerHistory = await verifyControllerHistoryPackage({ repositoryRoot, pluginRoot });
    return { artifact_bindings: artifactBindings, native_patches: nativePatches, controller_history_helper: controllerHistory, verified: true, node: process.versions.node, reproducible_artifacts: artifacts, bundled_dependency_count: roots.length - 1,
      public_tool_count: toolCount, contracts_sha256: originalSources.get('contracts.mjs'),
      controller_profile_sha256: sharedProfileHash,
      package_lock_sha256: originalSources.get('package-lock.json'),
      scope: 'Byte-for-byte JavaScript/notices rebuild and offline MCP contract discovery; native and physical qualification are separate.' };
  } finally { await rm(temporary, { recursive: true, force: true }); }
}

async function main(args) {
  if (args.length === 1 && args[0] === '--help') {
    process.stdout.write('Usage: node scripts/verify-openpnp-package.mjs [--package ABSOLUTE_PLUGIN_PATH]\nRebuilds in a temporary tree using installed locked dependencies; makes no source/package changes or network requests.\n');
    return;
  }
  if (args.length !== 0 && !(args.length === 2 && args[0] === '--package' && path.isAbsolute(args[1])))
    throw new Error('Use no arguments, --help, or --package ABSOLUTE_PLUGIN_PATH.');
  const result = await verifyOpenPnpPackage(args.length ? { pluginRoot: args[1] } : {});
  process.stdout.write(JSON.stringify(result, null, 2) + '\n');
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  main(process.argv.slice(2)).catch(error => { process.stderr.write(`OpenPnP package verification failed: ${error.message}\n`); process.exitCode = 1; });
}
