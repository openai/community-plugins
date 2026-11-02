// SPDX-License-Identifier: Apache-2.0
// Metadata/resource reachability, not a behavioral skill evaluator.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, realpathSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';
const plugin = fileURLToPath(new URL('../../plugins/openpnp/', import.meta.url));
const manifest = JSON.parse(readFileSync(resolve(plugin, 'references/skill-contracts.json'), 'utf8'));
const skill = manifest.skills.find(row => row.name === 'recover-openpnp-job');
const entry = resolve(plugin, skill.entrypoint);
const target = 'references/sensing-reconciliation.md';
test('recovery skill declares its reachable current sensing reference and typed request/read dependencies', () => {
  const linked = [...readFileSync(entry, 'utf8').matchAll(/\[[^\]]+\]\(([^\s)]+)\)/g)]
    .map(match => match[1].split('#')[0]).filter(link => link && !link.includes('://'))
    .map(link => realpathSync(resolve(dirname(entry), link)));
  assert.ok(linked.includes(realpathSync(resolve(plugin, target))), 'Current recovery route is installed and directly reachable');
  assert.ok(skill.references.includes(target), 'Skill dependency inventory must include its current sensing recovery reference');
  const reference = readFileSync(resolve(plugin, target), 'utf8');
  for (const name of ['openpnp_request_sensing_reconciliation', 'openpnp_get_sensing_reconciliation']) {
    assert.ok(TOOL_BY_NAME.has(name), 'Dependency has a current closed schema');
    assert.ok(skill.tool_names.includes(name), `Recovery dependency inventory omits ${name}`);
    assert.ok(skill.post_historical_tool_additions.includes(name), 'New dependency remains separate from historical cases');
  }
  const read = TOOL_BY_NAME.get('openpnp_get_sensing_reconciliation');
  assert.equal(read.mutating, false); assert.deepEqual(read.inputSchema.required, ['task_id']);
});
