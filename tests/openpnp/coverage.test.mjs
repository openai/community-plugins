import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
test('requirement coverage resolves all eighteen requirements to shipped skills, schemas, adapters and reproducible evidence', async () => {
  const coverage = JSON.parse(await readFile(path.join(root, 'tests/openpnp/coverage.json'), 'utf8'));
  assert.equal(coverage.hardware_qualified, false);
  assert.deepEqual(coverage.requirements.map(row => row.id), Array.from({ length: 18 }, (_, n) => `R${String(n + 1).padStart(2, '0')}`));
  const evidenceIds = new Set(coverage.evidence.map(item => item.id));
  const files = new Set([coverage.plan, coverage.schema_source, ...coverage.evidence.map(item => item.path)]);
  for (const row of coverage.requirements) {
    assert.ok(['partial', 'passed', 'deferred_hardware'].includes(row.status));
    assert.ok(row.implemented_scope && (row.status === 'passed' || row.remaining_scope));
    assert.ok(row.skills.length && row.tools.length && row.tests.length && row.native_adapters.length);
    assert.ok(row.acceptance_scenarios.every(id => /^A(?:0[1-9]|1[0-2])$/.test(id)));
    assert.ok(row.evidence_ids.length && row.evidence_ids.every(id => evidenceIds.has(id)));
    for (const tool of row.tools) { assert.ok(TOOL_BY_NAME.has(tool.name), `${row.id}: missing tool ${tool.name}`); files.add(tool.schema); }
    for (const file of [...row.skills, ...row.tests, ...row.native_adapters]) files.add(file);
  }
  for (const file of files) {
    assert.ok(!path.isAbsolute(file) && !file.split('/').includes('..'), `Expected repository-relative evidence reference: ${file}`);
    assert.equal((await stat(path.join(root, file))).isFile(), true, `Missing coverage reference ${file}`);
  }
  for (const evidence of coverage.evidence) {
    if (evidence.sha256 !== undefined) {
      assert.match(evidence.sha256, /^[a-f0-9]{64}$/);
      assert.equal(createHash('sha256').update(await readFile(path.join(root, evidence.path))).digest('hex'), evidence.sha256,
        `Evidence changed after qualification: ${evidence.id}`);
    }
  }
});
