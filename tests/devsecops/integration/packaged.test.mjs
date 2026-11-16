import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { plugin, read } from '../support/package.mjs';

test('deterministic ZIP starts outside its checkout and performs real Helm pass/fail/unknown reviews with secret redaction', { timeout: 8 * 60 * 1000 }, () => {
  assert(process.env.DEVSECOPS_TEST_CONTEXT, 'The master must prepare the real tool environment');
  const context = read(process.env.DEVSECOPS_TEST_CONTEXT);
  const script = fileURLToPath(new URL('../support/check_package.py', import.meta.url));
  const result = spawnSync(context.python, ['-I', '-B', script, plugin, process.env.DEVSECOPS_TEST_CONTEXT], {
    stdio: 'inherit', env: { ...process.env, ...context.environment, PYTHONDONTWRITEBYTECODE: '1' }, timeout: 7 * 60 * 1000,
  });
  assert.ifError(result.error);
  assert.equal(result.status, 0, `Packaged functional checks failed: ${result.signal || result.status}`);
});
