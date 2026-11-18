import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { VERSION, LINUX_VERSION, assertSupported, runtimeSpec, findNpmCli } from '../../../plugins/boundaryxp/scripts/launch.mjs';
const root = new URL('../../../plugins/boundaryxp/', import.meta.url);
const read = async name => JSON.parse(await fs.readFile(new URL(name, root), 'utf8'));

test('catalog, manifest and locked compiled release agree', async () => {
  const manifest = await read('.codex-plugin/plugin.json');
  const catalog = JSON.parse(await fs.readFile(new URL('../../../.agents/plugins/marketplace.json', import.meta.url), 'utf8'));
  assert.equal(catalog.plugins.find(p => p.name === manifest.name).source.path, './plugins/boundaryxp');
  const config = await read('.mcp.json');
  assert.deepEqual(config.mcpServers.boundaryxp.args, ['./scripts/launch.mjs']);
  assert.equal(config.mcpServers.boundaryxp.cwd, '.');
  for (const platform of ['win32', 'linux']) {
  const spec = runtimeSpec(platform);
  const lock = await read(`${spec.directory}/package-lock.json`);
  const pkg = lock.packages['node_modules/boundaryxp-mcp'];
  assert.equal(pkg.version, spec.version);
  assert.deepEqual(pkg.os, [platform]);
  if (platform === 'win32') assert.equal(pkg.integrity, 'sha512-8aL0J/ZfcvTkRSdMnuekrQmZaf3KdX9kJFzJc1gNmnw8dMS7Y0XBX9z7/xUI1Ae+4g1Qjknn8912UT/TSmMzRA==');
  if (platform === 'linux') assert.equal(pkg.integrity, 'sha512-ewM1kq3OvE55k+WDfLiXWzWB3lEezpKNHOvAjoP0J9/UILHO9gFmtbPvj/H/ptVpONmfDu3FdKCRchH2h64sag==');
  assert.equal((await read(`${spec.directory}/package.json`)).dependencies['boundaryxp-mcp'], spec.version);
  for (const item of Object.values(lock.packages)) {
    if (item.resolved) { assert.equal(new URL(item.resolved).hostname, 'registry.npmjs.org'); assert.match(item.integrity, /^sha512-/); }
  }
  }
  for (const name of ['README.md','README.zh-CN.md','LICENSE','THIRD-PARTY-NOTICES.md','examples/multi-dev.md']) await fs.access(new URL(name, root));
});

test('unsupported native hosts and old Node fail before installation', () => {
  for (const [platform, arch, version] of [['linux','arm64','24.19.0'],['darwin','arm64','24.19.0'],['win32','arm64','24.19.0'],['win32','x64','20.0.0'],['win32','x64','22.18.0']]) {
    assert.throws(() => assertSupported(platform, arch, version), /BXP_(PLATFORM|NODE)/);
  }
  assert.doesNotThrow(() => assertSupported('win32','x64','22.19.0'));
  assert.doesNotThrow(() => assertSupported('linux','x64','22.19.0','2.36'));
  assert.doesNotThrow(() => assertSupported('linux','x64','24.19.0','2.39'));
  for (const glibc of ['2.35', '', 'musl']) assert.throws(() => assertSupported('linux','x64','24.19.0',glibc), /BXP_LIBC/);
  assert.equal(runtimeSpec('win32').version, VERSION);
  assert.equal(runtimeSpec('linux').version, LINUX_VERSION);
  assert.throws(() => runtimeSpec('darwin'), /BXP_PLATFORM/);
});

test('npm is resolved from the actual Node distribution, not PATH', async () => {
  const npm = await findNpmCli();
  assert.equal((await fs.stat(npm)).isFile(), true);
  assert.match(npm.replaceAll('\\', '/'), /\/npm\/bin\/npm-cli.js$/);
});

test('installable plugin contains only wrapper, manifest, lock and documentation', async () => {
  const files = await fs.readdir(root, { recursive: true });
  for (const name of files) {
    assert(!/(?:^|[\\/])(?:node_modules|\.env|data-rc9|auth\.json)(?:$|[\\/])/.test(name), name);
    assert(!/\.(?:node|exe|dll|cpp|cxx|h|tgz|zip)$/i.test(name), name);
    const entry = new URL(name.replaceAll('\\','/'), root);
    if ((await fs.stat(entry)).isFile()) {
      const text = await fs.readFile(entry, 'utf8');
      assert(!/(?:gh[pousr]_[A-Za-z0-9]{20,}|npm_[A-Za-z0-9]{20,}|C:[/\\]Users[/\\]Administrator|E:[/\\]boundx)/.test(text), fileURLToPath(entry));
    }
  }
});
