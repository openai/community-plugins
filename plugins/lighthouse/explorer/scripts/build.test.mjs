import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('cold Explorer package retains full third-party license notices', async () => {
  const notices = await readFile(new URL('../dist/THIRD_PARTY_NOTICES.md', import.meta.url), 'utf8');
  for (const dependency of ['@modelcontextprotocol/sdk', '@modelcontextprotocol/ext-apps', 'smol-toml', 'zod']) {
    assert.ok(notices.includes(`## ${dependency} `), `Missing bundled dependency: ${dependency}`);
  }
  assert.match(notices, /Permission is hereby granted, free of charge/);
  assert.match(notices, /Copyright/i);
  assert.match(notices, /THE SOFTWARE IS PROVIDED "AS IS"/);
  assert.doesNotMatch(notices, /\/Users\/|internal\.api\.openai\.org/);
});

test('public lockfile contains canonical registry tarball URLs', async () => {
  const lock = JSON.parse(await readFile(new URL('../package-lock.json', import.meta.url), 'utf8'));
  for (const [location, dependency] of Object.entries(lock.packages)) {
    if (!dependency.resolved) continue;
    const name = location.split('node_modules/').at(-1);
    const basename = name.split('/').at(-1);
    assert.equal(dependency.resolved, `https://registry.npmjs.org/${name}/-/${basename}-${dependency.version}.tgz`);
  }
});
