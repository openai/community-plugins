import assert from 'node:assert/strict';
import { readFileSync, readdirSync, realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';

const root = fileURLToPath(new URL('../../plugins/openpnp/', import.meta.url));
const manifest = JSON.parse(readFileSync(resolve(root, 'references/skill-contracts.json'), 'utf8'));
const skillNames = [
  'setup-openpnp', 'commission-openpnp-machine', 'calibrate-openpnp-motion',
  'calibrate-openpnp-vision', 'configure-openpnp-tooling', 'configure-openpnp-feeders',
  'prepare-openpnp-job', 'align-openpnp-board', 'validate-openpnp-job', 'run-openpnp-job',
  'recover-openpnp-job', 'maintain-openpnp-machine', 'optimize-openpnp-production', 'backup-restore-openpnp',
];

function installedPath(path) {
  const result = realpathSync(resolve(root, path));
  const inside = relative(realpathSync(root), result);
  assert.ok(inside && !inside.startsWith(`..${sep}`) && inside !== '..' && !isAbsolute(inside), `Resource escapes installed plugin: ${path}`);
  assert.ok(statSync(result).isFile(), `Resource is not a file: ${path}`);
  return result;
}

function frontmatter(text) {
  const block = text.match(/^---\n([\s\S]*?)\n---\n/);
  assert.ok(block, 'Required YAML frontmatter');
  // These entrypoints deliberately use only scalar name and description fields.
  const result = Object.fromEntries(block[1].split('\n').map(line => {
    const field = line.match(/^([a-z-]+): (.+)$/);
    assert.ok(field, `Unexpected scalar frontmatter: ${line}`);
    return [field[1], field[2]];
  }));
  assert.deepEqual(Object.keys(result).sort(), ['description', 'name']);
  return result;
}

function uiMetadata(text) {
  const fields = {};
  let scope;
  for (const line of text.trim().split('\n')) {
    if (/^[a-z_]+:$/.test(line)) { scope = line.slice(0, -1); continue; }
    const match = line.match(/^  ([a-z_]+): (.+)$/);
    assert.ok(match && scope, `Invalid metadata line: ${line}`);
    fields[`${scope}.${match[1]}`] = JSON.parse(match[2]);
  }
  return fields;
}

function markdownFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = resolve(directory, entry.name);
    return entry.isDirectory() ? markdownFiles(path) : entry.name.endsWith('.md') ? [path] : [];
  });
}

test('all planned skill entrypoints resolve with discriminating frontmatter and UI metadata', () => {
  assert.deepEqual(manifest.skills.map(skill => skill.name).sort(), [...skillNames].sort());
  assert.equal(new Set(manifest.skills.map(skill => skill.name)).size, 14);
  const descriptions = new Set();
  for (const skill of manifest.skills) {
    const path = installedPath(skill.entrypoint);
    assert.equal(dirname(path), resolve(root, 'skills', skill.name));
    const yaml = frontmatter(readFileSync(path, 'utf8'));
    assert.equal(yaml.name, skill.name);
    assert.ok(yaml.description.length > 30 && yaml.description.length <= 1024);
    assert.ok(!descriptions.has(yaml.description), `Duplicate discovery description: ${skill.name}`);
    descriptions.add(yaml.description);
    const ui = uiMetadata(readFileSync(installedPath(skill.metadata), 'utf8'));
    assert.ok(ui['interface.display_name'].length > 0);
    assert.ok(ui['interface.short_description'].length >= 25 && ui['interface.short_description'].length <= 64);
    assert.ok(ui['interface.default_prompt'].includes(`$${skill.name}`));
    assert.notEqual(ui['policy.allow_implicit_invocation'], false, 'Automatic discovery remains enabled');
  }
});

test('skill references remain available in a sparse installed plugin with no escaping paths', () => {
  for (const skill of manifest.skills) for (const reference of skill.references) installedPath(reference);
  const files = [...markdownFiles(resolve(root, 'skills')), ...markdownFiles(resolve(root, 'references'))];
  let localLinks = 0;
  for (const file of files) {
    for (const match of readFileSync(file, 'utf8').matchAll(/\[[^\]]+\]\(([^\s)]+)\)/g)) {
      const target = match[1];
      if (target.startsWith('https://')) { assert.doesNotThrow(() => new URL(target)); continue; }
      assert.ok(!target.includes('://') && !isAbsolute(target), `Unsupported link in ${file}: ${target}`);
      const [path] = target.split('#');
      if (!path) continue;
      installedPath(relative(root, resolve(dirname(file), decodeURIComponent(path))));
      localLinks++;
    }
  }
  assert.ok(localLinks >= 28, 'Each skill links its control and domain references');
});

test('named tool dependencies are accounted for as current schemas or explicit conditional workflows', () => {
  const conditional = new Set(manifest.conditional_tool_names);
  assert.equal(conditional.size, manifest.conditional_tool_names.length);
  for (const name of conditional) assert.ok(!TOOL_BY_NAME.has(name), `Tool ${name} is implemented now; update the conditional dependency inventory`);
  const shared = new Set(manifest.shared_tool_names);
  for (const skill of manifest.skills) {
    const declared = new Set([...skill.tool_names, ...shared]);
    for (const name of readFileSync(installedPath(skill.entrypoint), 'utf8').match(/\bopenpnp_[a-z_]+\b/g) ?? []) {
      assert.ok(declared.has(name), `${skill.name} has an undeclared dependency: ${name}`);
    }
    for (const name of skill.tool_names) assert.ok(TOOL_BY_NAME.has(name) || conditional.has(name), `Unaccounted tool dependency: ${name}`);
  }
  for (const name of shared) assert.ok(TOOL_BY_NAME.has(name) || conditional.has(name), `Unaccounted shared tool: ${name}`);
});

test('plugin icon is self-contained vector media', () => {
  const icon = readFileSync(installedPath('assets/icon.svg'), 'utf8');
  assert.match(icon, /^<svg\s/);
  assert.match(icon, /viewBox="0 0 256 256"/);
  assert.doesNotMatch(icon, /<(?:script|foreignObject)\b|(?:href|src)\s*=|on[a-z]+\s*=/i);
});
