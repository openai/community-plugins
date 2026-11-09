import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
const root = fileURLToPath(new URL('../plugins/pcba-engineering/', import.meta.url));
const portable = JSON.parse(fs.readFileSync(path.join(root, 'plugin.json')));
const legacy = JSON.parse(fs.readFileSync(path.join(root, '.codex-plugin/plugin.json')));
assert.equal(portable.$schema, 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json');
for (const key of ['name', 'version', 'description', 'author', 'license']) assert.deepEqual(portable[key], legacy[key], `Manifest drift: ${key}`);
assert.equal(portable.name, 'pcba-engineering');
assert.deepEqual(portable.extensions['com.openai'].interface, legacy.interface);
assert.ok(legacy.interface.shortDescription.length <= 30);
assert.ok(legacy.interface.defaultPrompt.length <= 3);
for (const key of ['skills', 'mcpServers', 'apps', 'interface']) assert.equal(portable[key], undefined, `Portable manifest must not use ${key}`);
for (const skill of ['review-pcba-package', 'revise-pcba-assembly', 'prepare-pcba-handoff']) {
  const content = fs.readFileSync(path.join(root, 'skills', skill, 'SKILL.md'), 'utf8');
  assert.ok(content.startsWith(`---\nname: ${skill}\ndescription:`));
  assert.ok(content.includes('scripts/pcba.mjs'));
}
for (const asset of ['README.md', 'LICENSE', legacy.interface.logo, legacy.interface.composerIcon, 'references/workflow.md']) assert.ok(fs.statSync(path.resolve(root, asset)).isFile());
let modules = 0;
function walk(dir) {
  for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, item.name);
    assert.ok(!item.isSymbolicLink(), `Package must not contain a symlink: ${full}`);
    assert.ok(!['node_modules', '.env', '.git'].includes(item.name), `Unexpected package content: ${full}`);
    if (item.isDirectory()) walk(full);
    else if (item.name.endsWith('.mjs')) {
      const content = fs.readFileSync(full, 'utf8');
      assert.doesNotMatch(content, /(?:from\s+|import\s*\()['"][^'"\n]*openpnp/i, 'OpenPnP must not be a dependency');
      assert.doesNotMatch(content, /\/Users\/|\/home\//, 'Runtime code must not embed developer paths');
      const result = spawnSync(process.execPath, ['--check', full], { encoding: 'utf8' });
      assert.equal(result.status, 0, result.stderr); modules++;
    }
  }
}
walk(root);
console.log(`PCBA package validation passed (${modules} modules, 3 skills, synchronized manifests).`);
