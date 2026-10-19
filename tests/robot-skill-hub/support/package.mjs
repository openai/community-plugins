import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { cpSync, lstatSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const repo = fileURLToPath(new URL('../../../', import.meta.url));
export const packageRoot = path.join(repo, 'plugins/robot-skill-hub');
export const endpoint = 'https://api.superfastrobotics.com/mcp';
export const original = Object.freeze({
  'plugin.json': '39c5eb46c191476d5066cf6dec7c9ae7ce2264bc0f6256bec2d7a38ee87c1d54',
  'mcp.json': '2f55123efe1c6e44efd7c6239a3417a50b07254d2bb3d123074ec74859bbc7c3',
  LICENSE: 'a36626d945a25e7f2bcf1054b7798b22a2528b48bc5251d3fab34f36e56eeba2',
  'README.md': 'c45b4b7a664f5f31c0997d0cd2b4d53f655cb055c8e029baf93a7a62175bc05f',
});
export const communityReadme = { bytes: 2404, sha256: 'f435b3607d563460fb427e14b6b0c217457a2bab94fb3b6614a6f918b453692e' };
export const json = filename => JSON.parse(readFileSync(filename, 'utf8'));
export const hash = raw => createHash('sha256').update(raw).digest('hex');
export function testEnvironment(extra = {}) {
  const value = {};
  for (const name of ['PATH', 'Path', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'TMPDIR', 'TMP', 'TEMP']) if (process.env[name] !== undefined) value[name] = process.env[name];
  return { ...value, ...extra };
}

export function privateDirectory(t) {
  const prefix = path.join(realpathSync(tmpdir()), 'community-robot-skill-hub-');
  const root = realpathSync(mkdtempSync(prefix));
  assert.ok(root.startsWith(prefix) && path.dirname(root) === path.dirname(prefix));
  t.after(() => {
    assert.ok(root.startsWith(prefix) && path.dirname(root) === path.dirname(prefix));
    rmSync(root, { recursive: true, force: true });
  });
  return root;
}

export function copiedPackage(t) {
  const root = path.join(privateDirectory(t), 'plugin');
  cpSync(packageRoot, root, { recursive: true });
  return root;
}

function inventory(root, directory = root) {
  const files = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const filename = path.join(directory, entry.name);
    const stat = lstatSync(filename);
    assert.equal(stat.isSymbolicLink(), false, 'Package must not contain links');
    if (stat.isDirectory()) files.push(...inventory(root, filename));
    else { assert.ok(stat.isFile()); files.push(path.relative(root, filename).split(path.sep).join('/')); }
  }
  return files.sort();
}

// Test-side distribution audit. This is not a runtime module shipped to clients.
export function inspectPackage(root = packageRoot) {
  assert.deepEqual(inventory(root), ['.codex-plugin/plugin.json', 'COMMUNITY.md', 'LICENSE', 'README.md', 'mcp.json', 'plugin.json']);
  const portable = json(path.join(root, 'plugin.json'));
  const native = json(path.join(root, '.codex-plugin/plugin.json'));
  for (const field of ['name', 'version', 'description', 'author', 'homepage', 'repository', 'license']) assert.deepEqual(native[field], portable[field]);
  assert.equal(portable.name, 'robot-skill-hub');
  assert.equal(portable.license, 'MIT');
  assert.equal(native.mcpServers, './mcp.json');
  assert.equal(native.interface.category, 'Developer Tools');
  assert.deepEqual(native.interface.capabilities, ['Read']);
  for (const field of ['command', 'hooks', 'scripts', 'skills', 'apps', 'env', 'env_vars', 'headers']) assert.equal(Object.hasOwn(native, field), false, field);
  const config = json(path.join(root, native.mcpServers));
  assert.deepEqual(Object.keys(config).sort(), ['$schema', 'mcpServers']);
  assert.deepEqual(Object.keys(config.mcpServers), ['robot-skill-hub']);
  assert.deepEqual(config.mcpServers['robot-skill-hub'], { type: 'streamable-http', url: endpoint });
  const readme = readFileSync(path.join(root, 'README.md'));
  assert.equal(readme.length, 3422);
  for (const [filename, expected] of Object.entries(original)) assert.equal(hash(readFileSync(path.join(root, filename))), expected, filename);
  const community = readFileSync(path.join(root, 'COMMUNITY.md'));
  assert.equal(community.length, communityReadme.bytes);
  assert.equal(hash(community), communityReadme.sha256);
  return { portable, native, config, readme: readme.toString('utf8'), community: community.toString('utf8'), endpoint };
}
