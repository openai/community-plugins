import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const execute = promisify(execFile);
const stage = fileURLToPath(new URL('./stage-main.mjs', import.meta.url));
const repository = new URL('../../', import.meta.url);

test('staging replaces old routing and Explorer skills while preserving unrelated installed files', async (context) => {
  const temporary = await mkdtemp(join(tmpdir(), 'lighthouse-main-stage-'));
  context.after(() => rm(temporary, { recursive: true, force: true }));
  const previous = join(temporary, 'previous');
  const next = join(temporary, 'next');
  for (const folder of ['.codex-plugin', 'skills/lh', 'skills/lh-open-explorer', 'skills/lh-create-task', 'skills/lh-run-bench']) {
    await mkdir(join(previous, folder), {recursive: true});
  }
  const manifest = {name: 'lighthouse', version: '0.3.0', skills: './skills/', interface: {displayName: 'Personal Lighthouse', capabilities: ['Read']}};
  await writeFile(join(previous, '.codex-plugin/plugin.json'), JSON.stringify(manifest));
  const overlaid = ['skills/lh/SKILL.md', 'skills/lh-open-explorer/SKILL.md', 'skills/lh-create-task/SKILL.md'];
  for (const relative of overlaid) await writeFile(join(previous, relative), `Old release: ${relative}`);
  const unchanged = 'skills/lh-run-bench/SKILL.md';
  await writeFile(join(previous, unchanged), 'Keep the published Harbor workflow');

  await execute(process.execPath, [stage, previous, next]);

  for (const relative of overlaid) {
    assert.equal(await readFile(join(next, relative), 'utf8'), await readFile(new URL(relative, repository), 'utf8'));
    assert.equal(await readFile(join(previous, relative), 'utf8'), `Old release: ${relative}`);
  }
  assert.equal(await readFile(join(next, unchanged), 'utf8'), 'Keep the published Harbor workflow');
  const packaged = JSON.parse(await readFile(join(next, '.codex-plugin/plugin.json'), 'utf8'));
  assert.equal(packaged.version, manifest.version);
  assert.equal(packaged.interface.displayName, manifest.interface.displayName);
  assert.deepEqual(packaged.interface.capabilities, ['Read', 'Interactive']);
  assert.equal(packaged.mcpServers, './.mcp.json');
});
