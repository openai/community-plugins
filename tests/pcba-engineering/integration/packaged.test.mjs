import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';

const sourcePackage = fileURLToPath(new URL('../../../plugins/pcba-engineering/', import.meta.url));
const fixtures = fileURLToPath(new URL('../fixtures/', import.meta.url));
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

function coldPackage(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pcba-copied-package-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const installed = path.join(root, 'arbitrary location', 'installed plugin');
  fs.cpSync(sourcePackage, installed, { recursive: true, errorOnExist: true });
  const cwd = path.join(root, 'unrelated working directory');
  const bin = path.join(root, 'empty-bin');
  fs.mkdirSync(cwd);
  fs.mkdirSync(bin);
  const guard = path.join(root, 'deny-network.mjs');
  // Guard only external infrastructure, never the application's parsing, checks,
  // rendering or state operations. Node's permission model separately denies
  // reading the source checkout and spawning npm/other tools.
  fs.writeFileSync(guard, `import fs from 'node:fs';
import net from 'node:net';
import tls from 'node:tls';
import dns from 'node:dns';
import { syncBuiltinESMExports } from 'node:module';
const deny = (...args) => {
  fs.appendFileSync(${JSON.stringify(path.join(root, 'network-attempts.txt'))}, 'attempt\\n');
  throw new Error('Copied-package core attempted network access');
};
globalThis.fetch = deny;
net.connect = net.createConnection = net.Socket.prototype.connect = deny;
tls.connect = deny;
for (const api of [dns, dns.promises]) {
  for (const name of Object.keys(api)) {
    if (typeof api[name] === 'function' && /^(lookup|resolve|reverse)/.test(name)) api[name] = deny;
  }
}
syncBuiltinESMExports();
`);
  const permission = process.allowedNodeEnvironmentFlags.has('--permission') ? '--permission' : '--experimental-permission';
  const nodeArgs = [permission, `--allow-fs-read=${root}`, `--allow-fs-write=${root}`, '--import', guard];
  const env = { PATH: bin, LANG: 'C', LC_ALL: 'C', ...(process.env.SystemRoot ? { SystemRoot: process.env.SystemRoot } : {}) };
  function node(args) {
    const result = spawnSync(process.execPath, [...nodeArgs, ...args], { cwd, env, encoding: 'utf8', timeout: 30_000, maxBuffer: 8 * 1024 * 1024 });
    assert.ifError(result.error);
    assert.equal(result.signal, null, result.stderr);
    return result;
  }
  function cli(args, expected = 0) {
    const result = node([path.join(installed, 'scripts', 'pcba.mjs'), ...args]);
    assert.equal(result.status, expected, `command=${args[0]}\nstdout=${result.stdout}\nstderr=${result.stderr}`);
    const payload = JSON.parse(expected === 1 ? result.stderr : result.stdout);
    assert.equal(payload.ok, expected !== 1);
    return payload;
  }
  function copyFixture(name) {
    const destination = path.join(root, name.replaceAll('/', '-'));
    fs.copyFileSync(path.join(fixtures, name), destination);
    return destination;
  }
  return { root, installed, cwd, node, cli, copyFixture };
}

test('copied package statically resolves both manifests and all three skill entrypoints without repository assets', t => {
  const { installed } = coldPackage(t);
  const portable = JSON.parse(fs.readFileSync(path.join(installed, 'plugin.json'), 'utf8'));
  const compatibility = JSON.parse(fs.readFileSync(path.join(installed, '.codex-plugin', 'plugin.json'), 'utf8'));
  for (const field of ['name', 'version', 'description']) assert.equal(portable[field], compatibility[field]);
  assert.equal(portable.name, 'pcba-engineering');
  assert.equal(compatibility.skills, './skills/');
  const skillRoot = path.resolve(installed, compatibility.skills);
  const expected = ['prepare-pcba-handoff', 'review-pcba-package', 'revise-pcba-assembly'];
  assert.deepEqual(fs.readdirSync(skillRoot).sort(), expected);
  for (const name of expected) {
    const directory = path.join(skillRoot, name);
    const skill = fs.readFileSync(path.join(directory, 'SKILL.md'), 'utf8');
    assert.match(skill, new RegExp(`^---\\nname: ${name}\\n`, 'm'));
    const resolvedPluginRoot = path.resolve(directory, '../..');
    assert.equal(resolvedPluginRoot, installed);
    assert.ok(fs.statSync(path.join(resolvedPluginRoot, 'scripts', 'pcba.mjs')).isFile());
    assert.ok(fs.statSync(path.join(directory, 'agents', 'openai.yaml')).isFile());
    for (const [, relative] of skill.matchAll(/`(\.\.\/[^`]+)`/g)) {
      const target = path.resolve(directory, relative);
      assert.ok(target.startsWith(installed + path.sep), `${name}: ${relative} escapes package`);
      assert.ok(fs.statSync(target).isFile(), `${name}: ${relative} is not bundled`);
    }
  }
  for (const ui of [portable.extensions['com.openai'].interface, compatibility.interface]) {
    for (const key of ['logo', 'composerIcon']) assert.ok(fs.statSync(path.resolve(installed, ui[key])).isFile());
  }
  // This is package/metadata evidence only. Real Codex installation, discovery
  // and natural-language skill invocation require the separate host E2E.
});

test('cold copied CLI completes real sensor failure, repair, handoff and fresh-process resume with checkout, network and child tools unavailable', t => {
  const cold = coldPackage(t);
  const { root, installed, cli, node } = cold;
  const denied = node(['--input-type=module', '--eval', `import fs from 'node:fs';
try { fs.readFileSync(${JSON.stringify(path.join(sourcePackage, 'plugin.json'))}); process.exitCode = 9; }
catch (error) { console.log(error.code); }
`]);
  assert.equal(denied.status, 0);
  assert.equal(denied.stdout.trim(), 'ERR_ACCESS_DENIED', 'the child must be unable to read the original checkout');
  const doctor = cli(['doctor', '--kicad', path.join(root, 'not-installed', 'kicad-cli')]);
  assert.equal(doctor.coreReady, true);
  assert.equal(doctor.networkRequired, false);
  assert.equal(doctor.kicad.available, false);
  assert.equal(doctor.kicad.requiredForSuppliedExports, false);
  assert.equal(path.resolve(doctor.pluginRoot), installed);
  const input = cold.copyFixture('sparkfun-stcc4/missing-u3.json');
  const originalHash = sha(fs.readFileSync(input));
  const project = path.join(root, 'new assembly project');
  const imported = cli(['import', '--project', project, '--input', input]);
  assert.equal(imported.report.status, 'fail');
  assert.deepEqual(imported.report.findings.filter(f => f.severity === 'error').map(f => f.refs), [['U3']]);
  const checked = cli(['check', '--project', project], 2);
  assert.equal(checked.revision, imported.revision);
  assert.equal(cli(['export', '--project', project], 1).error.code, 'PACKAGE_INCONSISTENT');
  const plan = cli(['plan-change', '--project', project, '--use-authoritative', '--reason', 'Restore U3 from the supplied pinned source geometry.']);
  assert.equal(plan.changes.length, 1);
  assert.equal(plan.changes[0].ref, 'U3');
  assert.equal(plan.changes[0].before, null);
  assert.equal(cli(['resume', '--project', project]).revision, imported.revision, 'preview must preserve current revision');
  const applied = cli(['apply-change', '--project', project, '--plan', plan.planPath]);
  assert.equal(applied.report.status, 'pass');
  assert.equal(applied.report.scope.materialReadiness, 'unknown');
  assert.equal(applied.report.scope.geometryProvenance, 'supplied-source-not-authenticated');
  assert.equal(cli(['apply-change', '--project', project, '--plan', plan.planPath]).idempotent, true);
  assert.equal(cli(['check', '--project', project, '--expect', applied.revision]).report.status, 'pass');
  const delivery = cli(['export', '--project', project, '--output', path.join(root, 'offline delivery')]);
  const verified = cli(['verify-handoff', '--directory', delivery.output]);
  assert.equal(verified.valid, true);
  assert.equal(verified.revision, applied.revision);
  assert.ok(verified.checks.includes('independent-csv-readback'));
  const resumed = cli(['resume', '--project', project]);
  assert.equal(resumed.revision, applied.revision);
  assert.equal(resumed.history.length, 2);
  assert.equal(resumed.history[1].change.planId, plan.planId);
  assert.equal(sha(fs.readFileSync(input)), originalHash, 'supplied source remains unchanged');
  assert.ok(fs.readFileSync(delivery.guide, 'utf8').includes('U3'));
  assert.equal(fs.existsSync(path.join(root, 'network-attempts.txt')), false);
});

test('same cold package handles independent public board through supplied CSV export inputs without KiCad or fixture-specific code', t => {
  const cold = coldPackage(t);
  const { root, cli } = cold;
  const source = cold.copyFixture('opulo-ftp/clean.json');
  const original = JSON.parse(fs.readFileSync(source, 'utf8'));
  const first = cli(['import', '--project', path.join(root, 'opulo source'), '--input', source]);
  assert.equal(first.report.status, 'pass');
  const firstPacket = cli(['export', '--project', path.join(root, 'opulo source'), '--output', path.join(root, 'opulo source packet')]);
  const brief = JSON.parse(fs.readFileSync(path.join(firstPacket.output, 'brief.json'), 'utf8'));
  const briefFile = path.join(root, 'csv-import-brief.json');
  fs.writeFileSync(briefFile, JSON.stringify(brief));
  const project = path.join(root, 'independent CSV project');
  const imported = cli(['import', '--project', project, '--brief', briefFile,
    '--bom', path.join(firstPacket.output, 'population.csv'),
    '--placements', path.join(firstPacket.output, 'placements.csv'),
    '--authoritative', path.join(firstPacket.output, 'placements.csv'),
    '--units', 'mm', '--bottom-view', 'top-view']);
  assert.equal(imported.report.status, 'pass');
  assert.equal(imported.report.board.id, original.board.id);
  assert.equal(imported.report.counts.bom, original.bom.length);
  assert.equal(imported.report.counts.requiredPlacements, original.bom.filter(r => r.category === 'smt' && r.populated).length);
  assert.equal(cli(['doctor', '--kicad', path.join(root, 'no-cad')]).kicad.available, false);
  const delivered = cli(['export', '--project', project]);
  assert.equal(cli(['verify-handoff', '--directory', delivered.output]).valid, true);
  assert.equal(cli(['resume', '--project', project]).history.length, 1);
  assert.equal(fs.existsSync(path.join(root, 'network-attempts.txt')), false);
  // CSV is generated here to exercise the CLI exchange contract, not to claim
  // another independent CAD export/source-qualification run.
});
