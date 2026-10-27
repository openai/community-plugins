// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('OpenPnP lockfile uses public tarball URLs and integrity pins', async () => {
  const lock = JSON.parse(await readFile(new URL('../../src/openpnp/node/package-lock.json', import.meta.url), 'utf8'));
  const packages = Object.entries(lock.packages).filter(([name]) => name !== '');
  assert.ok(packages.length > 0, 'The lockfile must contain resolved dependencies');
  for (const [name, entry] of packages) {
    const resolved = new URL(entry.resolved);
    assert.equal(resolved.origin, 'https://registry.npmjs.org', `${name} must use the public registry`);
    assert.equal(resolved.username, '', `${name} must not embed credentials`);
    assert.equal(resolved.password, '', `${name} must not embed credentials`);
    assert.equal(resolved.search, '', `${name} must not embed query credentials`);
    assert.equal(resolved.hash, '', `${name} must not use URL fragments`);
    assert.match(entry.integrity, /^sha512-[A-Za-z0-9+/]+={0,2}$/, `${name} must pin tarball integrity`);
  }
});
