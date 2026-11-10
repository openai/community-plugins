import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { digest, importProject, projectStatus, planChange, applyChange, exportHandoff, verifyHandoff } from '../../../plugins/pcba-engineering/lib/project.mjs';
import { fixture, workspace } from '../support/project-fixture.mjs';
const writeJson = (file, data) => fs.writeFileSync(file, JSON.stringify(data, null, 2) + '\n');

test('changed CSV is rejected even when its manifest hash is recomputed', t => {
  const project = path.join(workspace(t), 'project');
  importProject(project, fixture('sparkfun-stcc4/clean'));
  const { output } = exportHandoff(project);
  const csv = path.join(output, 'placements.csv');
  const lines = fs.readFileSync(csv, 'utf8').split(/\r?\n/);
  const index = lines.findIndex(l => l.startsWith('U3,'));
  assert.ok(index > 0);
  const fields = lines[index].split(','); fields[1] = '5'; lines[index] = fields.join(',');
  fs.writeFileSync(csv, lines.join('\n'));
  const manifestFile = path.join(output, 'manifest.json'), manifest = JSON.parse(fs.readFileSync(manifestFile));
  const entry = manifest.files.find(f => f.path === 'placements.csv');
  const data = fs.readFileSync(csv); entry.sha256 = digest(data); entry.bytes = data.length;
  writeJson(manifestFile, manifest);
  assert.throws(() => verifyHandoff(output), e => e.code === 'EXPORT_MISMATCH');
});

test('tampered plan and revision are rejected without advancing project state', t => {
  const root = workspace(t), project = path.join(root, 'project');
  const imported = importProject(project, fixture('sparkfun-stcc4/missing-u3'));
  const plan = planChange(project, { useAuthoritative: true, reason: 'Restore only U3.' });
  const data = JSON.parse(fs.readFileSync(plan.planPath)); data.candidate.quantity = 100;
  const changed = path.join(root, 'changed-plan.json'); writeJson(changed, data);
  assert.throws(() => applyChange(project, changed), e => e.code === 'PLAN_TAMPERED');
  assert.equal(projectStatus(project).revision, imported.revision);
  const inputFile = path.join(project, 'revisions', imported.revision, 'input.json');
  const input = JSON.parse(fs.readFileSync(inputFile)); input.quantity = 100; writeJson(inputFile, input);
  assert.throws(() => projectStatus(project), e => e.code === 'REVISION_TAMPERED');
});

test('manifest omissions, traversal and symlink artifacts cannot pass verification', t => {
  const root = workspace(t), project = path.join(root, 'project');
  importProject(project, fixture('sparkfun-stcc4/clean'));
  const { output } = exportHandoff(project), manifestFile = path.join(output, 'manifest.json');
  const original = JSON.parse(fs.readFileSync(manifestFile));
  writeJson(manifestFile, { ...original, files: original.files.slice(1) });
  assert.throws(() => verifyHandoff(output), e => e.code === 'INVALID_MANIFEST');
  const traversal = structuredClone(original); traversal.files[0].path = '../input.json';
  writeJson(manifestFile, traversal);
  assert.throws(() => verifyHandoff(output), e => ['INVALID_MANIFEST', 'UNSAFE_PATH'].includes(e.code));
  writeJson(manifestFile, original);
  const artifact = path.join(output, 'placements.csv'), outside = path.join(root, 'outside.csv');
  fs.renameSync(artifact, outside); fs.symlinkSync(outside, artifact);
  assert.throws(() => verifyHandoff(output), e => e.code === 'UNSAFE_PATH');
});

test('project lock and symlinked project refuse writes', t => {
  const root = workspace(t), project = path.join(root, 'project');
  importProject(project, fixture('sparkfun-stcc4/clean'));
  fs.writeFileSync(path.join(project, '.pcba-lock'), JSON.stringify({ pid: process.pid }));
  assert.throws(() => planChange(project, { population: { U3: false }, reason: 'Test.' }), e => e.code === 'PROJECT_BUSY');
  const linked = path.join(root, 'linked'); fs.symlinkSync(project, linked);
  assert.throws(() => importProject(linked, fixture('sparkfun-stcc4/clean')), e => e.code === 'UNSAFE_PATH');
  assert.equal(projectStatus(project).history.length, 1);
});

test('default delivery path cannot escape through a project symlink', t => {
  const root = workspace(t), project = path.join(root, 'project'), outside = path.join(root, 'outside');
  fs.mkdirSync(outside);
  importProject(project, fixture('sparkfun-stcc4/clean'));
  fs.symlinkSync(outside, path.join(project, 'deliveries'));
  assert.throws(() => exportHandoff(project), e => e.code === 'UNSAFE_PATH');
  assert.deepEqual(fs.readdirSync(outside), []);
});

test('rehashed Markdown cannot add unsupported physical approval claims', t => {
  const project = path.join(workspace(t), 'project');
  importProject(project, fixture('sparkfun-stcc4/clean'));
  for (const name of ['handoff.md', 'inspection-plan.md']) {
    const { output } = exportHandoff(project, path.join(project, name + '-delivery'));
    const file = path.join(output, name), contents = '# Approved for production and electrically qualified.\n';
    fs.writeFileSync(file, contents);
    const manifestFile = path.join(output, 'manifest.json'), manifest = JSON.parse(fs.readFileSync(manifestFile));
    const entry = manifest.files.find(f => f.path === name); entry.sha256 = digest(contents); entry.bytes = Buffer.byteLength(contents);
    writeJson(manifestFile, manifest);
    assert.throws(() => verifyHandoff(output), e => e.code === 'HANDOFF_TEXT_MISMATCH');
  }
});

test('an empty output created during candidate verification is preserved', t => {
  const root = workspace(t), project = path.join(root, 'project'), output = path.join(root, 'delivery');
  importProject(project, fixture('sparkfun-stcc4/clean'));
  const read = fs.readFileSync;
  let injected = false, inode;
  fs.readFileSync = (file, ...args) => {
    if (!injected && String(file).includes('.pcba-export-') && path.basename(String(file)) === 'manifest.json') {
      injected = true; fs.mkdirSync(output); inode = fs.statSync(output).ino;
    }
    return read(file, ...args);
  };
  try { assert.throws(() => exportHandoff(project, output), e => e.code === 'OUTPUT_EXISTS'); }
  finally { fs.readFileSync = read; }
  assert.equal(injected, true);
  assert.equal(fs.statSync(output).ino, inode);
  assert.deepEqual(fs.readdirSync(output), []);
});
