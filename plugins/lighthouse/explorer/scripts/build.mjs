import { build } from 'esbuild';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const out = new URL('../dist/', import.meta.url);
await mkdir(out, { recursive: true });
const client = await build({
  absWorkingDir: root,
  entryPoints: ['src/ui/index.ts'],
  outfile: 'widget.js',
  bundle: true,
  format: 'iife',
  target: 'chrome120',
  minify: true,
  write: false,
  legalComments: 'eof',
  metafile: true,
});
const js = client.outputFiles.find((file) => file.path.endsWith('.js'))?.text;
const css = client.outputFiles.find((file) => file.path.endsWith('.css'))?.text ?? '';
if (!js) throw new Error('The browser build did not produce JavaScript');
const template = await readFile(new URL('../src/ui/index.html', import.meta.url), 'utf8');
const html = template.replace('<!-- STYLE -->', () => `<style>${css}</style>`).replace('<!-- SCRIPT -->', () => `<script>${js.replaceAll('</script', '<\\/script')}</script>`);
await writeFile(new URL('explorer.html', out), html);
const server = await build({
  absWorkingDir: root,
  entryPoints: ['src/server.ts'],
  outfile: 'dist/server.cjs',
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node22',
  minify: true,
  legalComments: 'eof',
  metafile: true,
});
const browser = await build({
  absWorkingDir: root,
  entryPoints: ['scripts/browser-launcher.mjs'],
  outfile: 'dist/browser.cjs',
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node22',
  minify: true,
  legalComments: 'eof',
  metafile: true,
});

// Ship full license/notice texts for every package included in any cold bundle.
// The metafiles keep this list tied to actual bundled inputs, not dev tooling.
const packageRoots = new Set();
for (const result of [client, server, browser]) {
  for (const input of Object.keys(result.metafile.inputs)) {
    const parts = input.split('/');
    const index = parts.lastIndexOf('node_modules');
    if (index < 0) continue;
    const length = parts[index + 1]?.startsWith('@') ? index + 3 : index + 2;
    packageRoots.add(parts.slice(0, length).join('/'));
  }
}
const notices = ['# Lighthouse Explorer third-party notices', '',
  'These packages are included in the generated Explorer bundles. Their original license and notice texts follow.', ''];
for (const directory of [...packageRoots].sort()) {
  const absolute = path.resolve(root, directory);
  const metadata = JSON.parse(await readFile(path.join(absolute, 'package.json'), 'utf8'));
  const files = (await readdir(absolute)).filter(name => /^(license|licence|copying|notice)([.-].*)?$/i.test(name)).sort();
  if (!files.some(name => /^(license|licence|copying)([.-].*)?$/i.test(name))) {
    throw new Error(`Bundled package ${metadata.name} is missing a license file`);
  }
  notices.push(`## ${metadata.name} ${metadata.version}`, '', `Declared license: ${metadata.license ?? 'see below'}`, '');
  for (const filename of files) {
    notices.push(`### ${filename}`, '', (await readFile(path.join(absolute, filename), 'utf8')).trimEnd(), '');
  }
}
await writeFile(new URL('THIRD_PARTY_NOTICES.md', out), notices.join('\n'));
