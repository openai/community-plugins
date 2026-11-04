import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const destination = process.argv[2];
if (!destination) throw new Error('Supply the personal lighthouse-explorer preview plugin directory.');
const target = resolve(destination);
const root = new URL('../../', import.meta.url);
const source = JSON.parse(await readFile(new URL('.codex-plugin/plugin.json', root), 'utf8'));
const sourceMcp = JSON.parse(await readFile(new URL('.mcp.json', root), 'utf8'));
const server = sourceMcp.mcpServers.lighthouse;
const manifest = {
  ...source,
  name: 'lighthouse-explorer',
  skills: './skills/',
  description: 'Read-only development preview of the Lighthouse benchmark Explorer.',
  interface: {
    ...source.interface,
    displayName: 'Lighthouse Explorer Preview',
    shortDescription: 'Browse local benchmark tasks, rubrics and recorded results.',
    longDescription: 'Open a visual read-only benchmark dashboard in Codex, with tasks, rubrics, leaderboard and quality reviews.',
    capabilities: ['Interactive', 'Read'],
    defaultPrompt: 'Open the native Lighthouse benchmark dashboard.',
  },
};
for (const directory of ['.codex-plugin', 'assets', 'explorer/dist', 'skills/lh-open-explorer']) {
  await mkdir(resolve(target, directory), { recursive: true });
}
await writeFile(resolve(target, '.codex-plugin/plugin.json'), `${JSON.stringify(manifest, null, 2)}\n`);
await writeFile(resolve(target, '.mcp.json'), `${JSON.stringify({mcpServers: {'lighthouse-explorer': {...server, env: {...server.env, LIGHTHOUSE_EXPLORER_TITLE: 'Lighthouse Explorer Preview'}}}}, null, 2)}\n`);
for (const name of ['assets/lighthouse-icon.svg', 'assets/lighthouse-icon-dark.svg', 'explorer/dist/server.cjs', 'explorer/dist/browser.cjs', 'explorer/dist/explorer.html', 'skills/lh-open-explorer/SKILL.md']) {
  await copyFile(fileURLToPath(new URL(name, root)), resolve(target, name));
}
