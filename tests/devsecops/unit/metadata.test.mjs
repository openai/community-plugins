import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { repo, plugin, read } from '../support/package.mjs';

test('catalog dispatch resolves the installed package and its complete native skills', () => {
  const compatibility = read(path.join(plugin, '.codex-plugin/plugin.json'));
  const portable = read(path.join(plugin, 'plugin.json'));
  for (const key of ['name', 'version', 'description']) assert.equal(portable[key], compatibility[key]);
  assert.equal(compatibility.name, 'devsecops');
  assert.equal(compatibility.license, 'Apache-2.0');
  assert.match(compatibility.version, /^\d+\.\d+\.\d+(?:-[\w.-]+)?$/);
  const entry = read(path.join(repo, '.agents/plugins/marketplace.json')).plugins.find(row => row.name === compatibility.name);
  assert.equal(entry.source.path, './plugins/devsecops');
  assert.equal(entry.category, compatibility.interface.category);
  assert.equal(read(path.join(repo, 'package.json')).scripts['test:devsecops'], 'node tests/devsecops/run.mjs');
  const skills = ['diagnose-gitops-deployment', 'prepare-gitops-correction', 'review-kubernetes-change', 'run-software-factory-demo', 'verify-gitops-recovery'];
  assert.deepEqual(fs.readdirSync(path.join(plugin, 'skills')).sort(), skills);
  for (const name of skills) {
    const directory = path.join(plugin, 'skills', name);
    const text = fs.readFileSync(path.join(directory, 'SKILL.md'), 'utf8');
    assert.match(text, new RegExp(`^---\\nname: ${name}\\n`));
    assert(fs.statSync(path.join(directory, 'agents/openai.yaml')).isFile());
    for (const [, link] of text.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
      const target = link.split('#')[0];
      if (!target || /^[a-z][a-z0-9+.-]*:/i.test(target)) continue;
      const resolved = path.resolve(directory, target);
      assert(resolved.startsWith(plugin + path.sep), `${name}: runtime reference escapes package`);
      assert(fs.existsSync(resolved), `${name}: missing runtime reference ${target}`);
    }
  }
  for (const key of ['logo', 'composerIcon']) assert(fs.statSync(path.resolve(plugin, compatibility.interface[key])).isFile());
  assert(compatibility.interface.defaultPrompt.every(prompt => prompt.length <= 128));
  for (const file of ['LICENSE', 'NOTICE', 'uv.lock', 'tests/Dockerfile', 'docs/community-preview.md',
    'scripts/bootstrap.py', 'src/devsecops/schema_catalog.py', 'demo/software-factory/config/registry1-blob-download-policy.example.json'])
    assert(fs.statSync(path.join(plugin, file)).isFile(), `Missing cold-start asset ${file}`);
  assert.deepEqual(fs.readdirSync(path.join(plugin, 'demo/portable-gitops')).sort(),
    ['README.md', 'acquire.sh', 'bundle_inputs.py', 'pipeline.py', 'scan_policy.py', 'stack.py', 'versions.json']);
});
