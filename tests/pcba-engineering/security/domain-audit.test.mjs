import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { normalizePackage, checkPackage } from '../../../plugins/pcba-engineering/lib/domain.mjs';
import { importProject, projectStatus, planChange, applyChange, exportHandoff, verifyHandoff, digest } from '../../../plugins/pcba-engineering/lib/project.mjs';

const fixturePath = fileURLToPath(new URL('../fixtures/sparkfun-stcc4/clean.json', import.meta.url));
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const write = (file, value) => fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n');
function setup(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pcba-security-audit-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const project = path.join(root, 'project');
  const pkg = read(fixturePath);
  const result = importProject(project, pkg);
  assert.equal(result.report.status, 'pass', 'the pinned independent source is valid before attacks');
  return { root, project, pkg, revision: result.revision };
}
function rehashPlan(project, planned, mutate) {
  const plan = read(planned.planPath);
  mutate(plan);
  plan.candidate = normalizePackage(plan.candidate);
  plan.candidateInputSha256 = digest(plan.candidate);
  plan.after = checkPackage(plan.candidate);
  const { planId, ...payload } = plan;
  plan.planId = digest(payload);
  const file = path.join(project, 'plans', `${plan.planId}.json`);
  write(file, plan);
  return file;
}
function rewriteArtifact(directory, name, data) {
  const manifest = read(path.join(directory, 'manifest.json'));
  fs.writeFileSync(path.join(directory, name), data);
  const entry = manifest.files.find(f => f.path === name);
  assert.ok(entry, 'test targets an actual declared artifact');
  entry.sha256 = digest(data); entry.bytes = Buffer.byteLength(data);
  write(path.join(directory, 'manifest.json'), manifest);
}

test('a rehashed stored plan cannot smuggle source or project identity changes into a population edit', t => {
  const { project, pkg, revision } = setup(t);
  const ref = pkg.bom.find(r => r.category === 'smt' && r.populated).ref;
  const planned = planChange(project, { population: { [ref]: false }, reason: 'Make an explicitly reviewed DNP population.' });
  for (const mutate of [
    plan => plan.candidate.quantity = 900,
    plan => plan.candidate.board.name = 'Forged board identity',
    plan => plan.candidate.source.forgedClaim = 'Unapproved source substitution',
    plan => plan.candidate.authoritativePlacements[0].xMm += 1,
    plan => plan.candidate.sourceFindings = [],
    plan => {
      const row = plan.candidate.bom.find(r => r.ref === ref);
      row.mpn = 'UNAPPROVED-PART';
      plan.changes.find(c => c.collection === 'bom' && c.ref === ref).after = structuredClone(row);
    },
  ]) {
    const forged = rehashPlan(project, planned, mutate);
    assert.throws(() => applyChange(project, forged), error => Boolean(error.code), 'internally consistent hashes cannot authorize edits outside the previewed operation');
    assert.equal(projectStatus(project).revision, revision, 'rejection leaves the project revision unchanged');
  }
  const applied = applyChange(project, planned.planPath);
  assert.equal(applied.applied, true, 'the original legitimate bounded plan still applies');
});

test('guide and vector drawing are verified semantically even after their manifest hashes are updated', t => {
  const { root, project } = setup(t);
  for (const [name, data] of [
    ['assembly-guide.html', '<html><body>APPROVED FOR MANUFACTURING</body></html>'],
    ['assembly-view.svg', '<svg xmlns="http://www.w3.org/2000/svg"><text>ALL PARTS PRESENT</text></svg>'],
  ]) {
    const dest = path.join(root, name.replaceAll('.', '-'));
    exportHandoff(project, dest);
    assert.equal(verifyHandoff(dest).valid, true);
    rewriteArtifact(dest, name, data);
    assert.throws(() => verifyHandoff(dest), error => Boolean(error.code), 'a matching hash must not hide a substituted guide or drawing');
  }
});

test('a wrong placement row with a fresh manifest hash fails independent CSV readback', t => {
  const { root, project } = setup(t);
  const dest = path.join(root, 'wrong-csv'); exportHandoff(project, dest);
  const name = 'placements.csv';
  const before = fs.readFileSync(path.join(dest, name), 'utf8');
  assert.match(before, /(?:^|\n)U3,/);
  rewriteArtifact(dest, name, before.replace(/(^|\n)U3,/, '$1BOGUS,'));
  assert.throws(() => verifyHandoff(dest), error => error.code === 'EXPORT_MISMATCH');
});

test('exports and bounded revisions leave the imported engineering file untouched', t => {
  const { root, project, pkg } = setup(t);
  const before = fs.readFileSync(fixturePath);
  const sourceHashes = pkg.source;
  const ref = pkg.bom.find(r => r.category === 'smt' && r.populated).ref;
  const planned = planChange(project, { population: { [ref]: false }, reason: 'Explicit variant test.' });
  applyChange(project, planned.planPath);
  const dest = path.join(root, 'untouched-source'); exportHandoff(project, dest);
  assert.deepEqual(fs.readFileSync(fixturePath), before);
  assert.deepEqual(read(path.join(dest, 'input.json')).source, sourceHashes);
});
