import assert from 'node:assert/strict';
import test from 'node:test';
import { inspectPackage } from '../support/package.mjs';

test('retains the published portable package and exposes one read-only native configuration', () => {
  const value = inspectPackage();
  assert.equal(value.native.version, '0.1.0');
  assert.match(value.readme, /read-only MCP plugin/);
  assert.match(value.community, /First safe prompt/);
  assert.match(value.community, /Do not[\s\S]*send private/);
  assert.match(value.community, /No Codex installed-client result/);
});
