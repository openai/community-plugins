import { copyFile, cp, mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const [previous, destination] = process.argv.slice(2);
if (!previous || !destination) throw new Error('Supply the previous personal Lighthouse release and a new staging directory.');
const origin = resolve(previous);
const target = resolve(destination);
if (origin === target) throw new Error('The previous release must be preserved.');
const exists = await stat(target).then(() => true, (error) => { if (error.code === 'ENOENT') return false; throw error; });
if (exists) throw new Error('Use a new staging directory.');
const root = new URL('../../', import.meta.url);
const current = JSON.parse(await readFile(new URL('.codex-plugin/plugin.json', root), 'utf8'));
const existing = JSON.parse(await readFile(resolve(origin, '.codex-plugin/plugin.json'), 'utf8'));
if (existing.name !== 'lighthouse') throw new Error('The original plugin must be Lighthouse.');

await cp(origin, target, {recursive: true, force: false, errorOnExist: true});
for (const directory of ['assets', 'explorer/dist', 'skills/lh', 'skills/lh-open-explorer', 'skills/lh-create-task']) {
  await mkdir(resolve(target, directory), {recursive: true});
}
const manifest = {
  ...existing,
  mcpServers: './.mcp.json',
  interface: {
    ...existing.interface,
    capabilities: [...new Set([...(existing.interface.capabilities ?? []), 'Interactive'])],
    composerIcon: current.interface.composerIcon,
    logo: current.interface.logo,
    logoDark: current.interface.logoDark,
  },
};
await writeFile(resolve(target, '.codex-plugin/plugin.json'), `${JSON.stringify(manifest, null, 2)}\n`);
for (const name of ['.mcp.json', 'assets/lighthouse-icon.svg', 'assets/lighthouse-icon-dark.svg', 'explorer/dist/server.cjs', 'explorer/dist/browser.cjs', 'explorer/dist/explorer.html', 'skills/lh/SKILL.md', 'skills/lh-open-explorer/SKILL.md', 'skills/lh-create-task/SKILL.md']) {
  await copyFile(fileURLToPath(new URL(name, root)), resolve(target, name));
}
