import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { plugin, privacyProblems } from '../support/package.mjs';

test('the distributable contains no operator state, personal paths, recordings or private coordination links', () => {
  assert.deepEqual(privacyProblems(plugin), []);
});

test('privacy validation rejects accidental operator artifacts and symlink escapes', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'devsecops-privacy-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, 'README.md'), 'Synthetic public fixture.');
  assert.deepEqual(privacyProblems(root), []);
  fs.writeFileSync(path.join(root, '.env'), 'SYNTHETIC_TEST_ONLY=canary');
  fs.writeFileSync(path.join(root, 'operator.md'), '/Users/synthetic-person/private/report.json');
  const failures = privacyProblems(root);
  assert(failures.some(item => item.includes('private/generated artifact')));
  assert(failures.some(item => item.includes('personal absolute path')));
  fs.symlinkSync(path.join(root, 'README.md'), path.join(root, 'alias'));
  assert.throws(() => privacyProblems(root), /Symlink/);
});
