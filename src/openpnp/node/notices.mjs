import { readFile, writeFile, readdir, mkdir, copyFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const source = path.dirname(fileURLToPath(import.meta.url));
const plugin = path.resolve(source, '../../../plugins/openpnp');
const inputs = JSON.parse(await readFile(path.join(source, 'build-inputs.json'), 'utf8'));
const roots = new Set(inputs.filter(file => file.startsWith('node_modules/')).map(file => {
  const segments = file.split('/'); return segments[1].startsWith('@') ? segments.slice(0, 3).join('/') : segments.slice(0, 2).join('/');
}));
let markdown = '# Third-party notices\n\nThe bundled MCP JavaScript contains the following dependencies. Exact resolution is locked in `src/openpnp/node/package-lock.json`; build input inventory is in `src/openpnp/node/build-inputs.json`.\n\n';
for (const directory of [...roots].sort()) {
  const packageRoot = path.join(source, directory);
  const metadata = JSON.parse(await readFile(path.join(packageRoot, 'package.json'), 'utf8'));
  const candidates = (await readdir(packageRoot)).filter(name => /^(license|licence|copying)(\.(md|txt))?$/i.test(name));
  if (!candidates.length) throw new Error(`No license text found for ${metadata.name}`);
  const license = await readFile(path.join(packageRoot, candidates[0]), 'utf8');
  markdown += `## ${metadata.name} ${metadata.version}\n\nLicense: ${typeof metadata.license === 'string' ? metadata.license : JSON.stringify(metadata.license)}\n\n\`\`\`text\n${license.trim()}\n\`\`\`\n\n`;
}
markdown += '## OpenPnP native bridge\n\nThe Java bridge is GPL-3.0-or-later and links against OpenPnP. The OpenPnP license is included in `licenses/OpenPnP-GPL-3.0.txt`. OpenPnP and its dependency JARs remain a separately provisioned runtime with their own license notices. The build pins commit `5bd404cfc70f34103a3ca0fbb6b50c2b465f407c`.\n';
await writeFile(path.join(plugin, 'THIRD_PARTY_NOTICES.md'), markdown);
await mkdir(path.join(plugin, 'licenses'), { recursive: true });
console.log(`Wrote notices for ${roots.size} bundled dependencies.`);
