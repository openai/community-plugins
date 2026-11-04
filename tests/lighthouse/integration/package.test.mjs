import assert from 'node:assert/strict';
import { readFile, readdir, access } from 'node:fs/promises';
import test from 'node:test';

test('skills-only package exposes seven skills without UI or MCP dependencies', async () => {
  const root = 'plugins/lighthouse';
  const manifest = JSON.parse(await readFile(`${root}/.codex-plugin/plugin.json`, 'utf8'));
  assert.equal(manifest.skills, './skills/');
  assert.equal(manifest.mcpServers, undefined);
  assert.ok(!manifest.interface.capabilities.includes('Interactive'));
  assert.equal((await readdir(`${root}/skills`)).length, 7);
  for (const path of ['.mcp.json', 'explorer', 'skills/lh-open-explorer']) {
    await assert.rejects(access(`${root}/${path}`), { code: 'ENOENT' });
  }
  const router = await readFile(`${root}/skills/lh/SKILL.md`, 'utf8');
  assert.doesNotMatch(router, /lighthouse_open_task_authoring|lh-open-explorer/);
  const scaffold = await readFile(`${root}/skills/lh-create-bench/SKILL.md`, 'utf8');
  assert.doesNotMatch(scaffold, /\.\.\/\.\.\/explorer\//);
});
