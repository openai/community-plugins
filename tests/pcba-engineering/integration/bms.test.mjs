import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { planChange, applyChange, exportHandoff, verifyHandoff, projectStatus, digest } from '../../../plugins/pcba-engineering/lib/project.mjs';
import { workspace } from '../support/project-fixture.mjs';

const fixtureRoot = fileURLToPath(new URL('../fixtures/libresolar-bms/', import.meta.url));
const cli = fileURLToPath(new URL('../../../plugins/pcba-engineering/scripts/pcba.mjs', import.meta.url));

test('BMS public revision change: import actual CSV inputs, restore only D11/D12, preserve identities and verify the handoff', t => {
  const root = workspace(t), project = path.join(root, 'bms-project');
  const source = JSON.parse(fs.readFileSync(path.join(fixtureRoot, 'source-manifest.json')));
  for (const item of source.files) assert.equal(digest(fs.readFileSync(path.join(fixtureRoot, item.path))), item.sha256);
  const input = JSON.parse(fs.readFileSync(path.join(fixtureRoot, 'revision-mismatch.json')));
  const env = { ...process.env, NODE_NO_WARNINGS: '1' }; delete env.NODE_OPTIONS;
  const imported = spawnSync(process.execPath, [cli, 'import', '--project', project,
    '--brief', path.join(fixtureRoot, 'brief.json'), '--bom', path.join(fixtureRoot, 'bom-v0.2.csv'),
    '--placements', path.join(fixtureRoot, 'placements-v0.1.1.csv'),
    '--authoritative', path.join(fixtureRoot, 'source-placements-v0.2.csv'),
    '--mapping', path.join(fixtureRoot, 'columns.json'), '--units', 'mm', '--bottom-view', 'top-view'], { encoding: 'utf8', env });
  assert.equal(imported.status, 0, imported.stderr);
  assert.equal(JSON.parse(imported.stdout).report.status, 'fail');
  const before = projectStatus(project);
  assert.deepEqual(before.report.findings.filter(f => f.severity === 'error').flatMap(f => f.refs).sort(), ['D11', 'D12']);
  const plan = planChange(project, { useAuthoritative: true, reason: 'Reconcile the earlier placement export with the published v0.2 BOM and board.' });
  assert.deepEqual(plan.changes.map(r => r.ref).sort(), ['D11', 'D12']);
  assert.ok(plan.sourceRowChanges.length > 90, 'inserting the two CSV rows shifts later evidence lines, not component coordinates');
  for (const row of plan.changes) assert.equal(row.before, null);
  const applied = applyChange(project, plan.planPath);
  assert.equal(applied.report.status, 'pass');
  const actual = JSON.parse(fs.readFileSync(path.join(project, 'revisions', applied.revision, 'input.json')));
  assert.equal(actual.placements.length, 147);
  const positions = rows => rows.map(({ref,xMm,yMm,rotationDeg,side,package:footprint}) => ({ref,xMm,yMm,rotationDeg,side,package:footprint}));
  assert.deepEqual(positions(actual.placements), positions(input.authoritativePlacements));
  assert.deepEqual(actual.sourceFindings, input.sourceFindings);
  assert.deepEqual(actual.bom.map(({ref,mpn,populated,value}) => ({ref,mpn,populated,value})), input.bom.map(({ref,mpn,populated,value}) => ({ref,mpn,populated,value})));
  const renumbered = actual.placements.map(row => ({ ...row, sourceRow: row.sourceRow + 100 }));
  assert.throws(() => planChange(project, { placements: renumbered, reason: 'Reformatted CSV only.' }), error => error.code === 'NO_CHANGE');
  const rotated = structuredClone(renumbered); rotated.find(row => row.ref === 'D11').rotationDeg += 90;
  const rotationPlan = planChange(project, { placements: rotated, reason: 'Preview an actual orientation difference.' });
  assert.deepEqual(rotationPlan.changes.map(row => row.ref), ['D11']);
  assert.equal(projectStatus(project).revision, applied.revision, 'a preview leaves the verified revision unchanged');
  const verified = verifyHandoff(exportHandoff(project).output);
  assert.equal(verified.valid, true);
  assert.equal(verified.filesChecked, 11);
});
