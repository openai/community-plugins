import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { importProject, projectStatus, checkProject, planChange, applyChange, exportHandoff, verifyHandoff, digest } from '../../../plugins/pcba-engineering/lib/project.mjs';
import { fixture, workspace } from '../support/project-fixture.mjs';
const cli = new URL('../../../plugins/pcba-engineering/scripts/pcba.mjs', import.meta.url).pathname;
const cliEnv = { ...process.env };
delete cliEnv.NODE_OPTIONS; // Keep host-injected loader warnings out of the CLI error-contract assertion.
cliEnv.NODE_NO_WARNINGS = '1'; // Node 22's ambient proxy warning is outside this CLI JSON contract.

test('real sensor package: detect missing U3, preview exactly one restoration, recheck and independently read delivered files', t => {
  const root = workspace(t), project = path.join(root, 'project');
  const input = fixture('sparkfun-stcc4/missing-u3'), before = structuredClone(input);
  const imported = importProject(project, input);
  assert.deepEqual(input, before);
  assert.equal(imported.report.status, 'fail');
  assert.deepEqual(imported.report.findings.filter(f => f.severity === 'error').map(f => f.refs), [['U3']]);
  assert.throws(() => exportHandoff(project), e => e.code === 'PACKAGE_INCONSISTENT');
  const plan = planChange(project, { useAuthoritative: true, reason: 'Restore missing U3 from supplied pinned source geometry.' });
  assert.equal(plan.changes.length, 1);
  assert.equal(plan.changes[0].ref, 'U3');
  assert.equal(plan.changes[0].before, null);
  assert.equal(checkProject(project).report.status, 'fail', 'preview must not mutate project');
  const applied = applyChange(project, plan.planPath);
  assert.equal(applied.report.status, 'pass');
  assert.equal(applied.report.scope.materialReadiness, 'unknown');
  assert.equal(applied.report.scope.electricalQualification, 'not_assessed');
  assert.equal(applyChange(project, plan.planPath).idempotent, true);
  assert.equal(projectStatus(project).history.length, 2);
  assert.equal(checkProject(project, applied.revision).report.status, 'pass');
  assert.throws(() => checkProject(project, imported.revision), e => e.code === 'STALE_REVISION');
  const delivery = exportHandoff(project);
  assert.equal(verifyHandoff(delivery.output).valid, true);
  assert.match(fs.readFileSync(delivery.guide, 'utf8'), /U3/);
  assert.equal(JSON.parse(fs.readFileSync(path.join(project, 'revisions', imported.revision, 'input.json'))).placements.some(r => r.ref === 'U3'), false);
  const hash = digest(fs.readFileSync(path.join(delivery.output, 'manifest.json')));
  assert.throws(() => exportHandoff(project), e => e.code === 'OUTPUT_EXISTS');
  assert.equal(hash, digest(fs.readFileSync(path.join(delivery.output, 'manifest.json'))));
});

test('independent Opulo training board imports and exports without dataset-specific code', t => {
  const project = path.join(workspace(t), 'project');
  const imported = importProject(project, fixture('opulo-ftp/clean'));
  assert.equal(imported.report.status, 'pass');
  assert.equal(verifyHandoff(exportHandoff(project).output).valid, true);
});

test('stale plans fail while a previous verified handoff and project remain intact', t => {
  const root = workspace(t), project = path.join(root, 'project');
  importProject(project, fixture('sparkfun-stcc4/clean'));
  const handoff = exportHandoff(project);
  const planA = planChange(project, { population: { U3: false }, reason: 'Explicitly exclude U3 for this controlled variant test.' });
  const planB = planChange(project, { population: { R3: false }, reason: 'Explicitly exclude R3 for this separate variant test.' });
  applyChange(project, planB.planPath);
  const revision = projectStatus(project).revision;
  assert.throws(() => applyChange(project, planA.planPath), e => e.code === 'STALE_PLAN');
  assert.equal(projectStatus(project).revision, revision);
  assert.equal(verifyHandoff(handoff.output).valid, true);
});

test('fresh external process resumes persisted state and reports unsupported flags', t => {
  const root = workspace(t), project = path.join(root, 'project');
  const imported = importProject(project, fixture('sparkfun-stcc4/missing-u3'));
  const result = JSON.parse(execFileSync(process.execPath, [cli, 'resume', '--project', project], { cwd: root, encoding: 'utf8', env: cliEnv }));
  assert.equal(result.revision, imported.revision);
  assert.equal(result.report.counts.errors, 1);
  const failed = spawnSync(process.execPath, [cli, 'check', '--project', project], { cwd: root, encoding: 'utf8', env: cliEnv });
  assert.equal(failed.status, 2);
  assert.equal(JSON.parse(failed.stdout).report.status, 'fail');
  const unknown = spawnSync(process.execPath, [cli, 'status', '--project', project, '--fix-everything'], { cwd: root, encoding: 'utf8', env: cliEnv });
  assert.equal(unknown.status, 1);
  assert.equal(JSON.parse(unknown.stderr).error.code, 'INVALID_ARGUMENT');
});

test('no coordinate invention when a newly populated reference lacks source geometry', t => {
  const project = path.join(workspace(t), 'project'), input = fixture('sparkfun-stcc4/clean');
  delete input.authoritativePlacements;
  input.bom.find(r => r.ref === 'U3').populated = false;
  input.placements = input.placements.filter(r => r.ref !== 'U3');
  importProject(project, input);
  assert.throws(() => planChange(project, { population: { U3: true }, reason: 'Populate the sensor.' }), e => e.code === 'AUTHORITATIVE_SOURCE_REQUIRED');
  assert.throws(() => planChange(project, { useAuthoritative: true, reason: 'Repair export.' }), e => e.code === 'AUTHORITATIVE_SOURCE_REQUIRED');
  assert.equal(projectStatus(project).history.length, 1);
});

test('case-insensitive authoritative references produce one precise restoration', t => {
  const project = path.join(workspace(t), 'project'), input = fixture('sparkfun-stcc4/missing-u3');
  input.authoritativePlacements.forEach(r => { r.ref = r.ref.toLowerCase(); });
  importProject(project, input);
  const plan = planChange(project, { useAuthoritative: true, reason: 'Restore U3 from the same source with lowercase reference labels.' });
  assert.equal(plan.changes.length, 1);
  assert.equal(plan.after.status, 'pass');
  assert.equal(plan.changes[0].ref, 'U3');
  assert.equal(applyChange(project, plan.planPath).report.status, 'pass');
});

test('pointer-write interruption preserves current state and safely recovers an identical orphan on retry', t => {
  const project = path.join(workspace(t), 'project');
  const imported = importProject(project, fixture('sparkfun-stcc4/missing-u3'));
  const plan = planChange(project, { useAuthoritative: true, reason: 'Restore U3.' });
  const rename = fs.renameSync;
  let injected = false;
  fs.renameSync = (from, to) => {
    if (!injected && to === path.join(fs.realpathSync(project), 'project.json')) { injected = true; throw Object.assign(new Error('Injected pointer write failure'), { code: 'EIO' }); }
    return rename(from, to);
  };
  try { assert.throws(() => applyChange(project, plan.planPath), e => e.code === 'EIO'); }
  finally { fs.renameSync = rename; }
  assert.equal(projectStatus(project).revision, imported.revision);
  const retried = applyChange(project, plan.planPath);
  assert.equal(retried.recovered, true);
  assert.equal(retried.report.status, 'pass');
  assert.equal(projectStatus(project).history.length, 2);
  assert.equal(verifyHandoff(exportHandoff(project).output).valid, true);
});
