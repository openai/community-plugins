/** Protocol/transaction tests with a fake executable. These do not qualify native KiCad. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { findKicad, exportKicad, KicadError } from '../../../plugins/pcba-engineering/lib/kicad.mjs';

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
function setup(t, mode = 'success') {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pcba-kicad-protocol-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const board = path.join(root, 'board with spaces;$(echo INERT).kicad_pcb');
  const schematic = path.join(root, 'board.kicad_sch');
  const output = path.join(root, 'native output');
  const kicad = path.join(root, 'fake-kicad-protocol.mjs');
  fs.writeFileSync(board, '(kicad_pcb (version 20260128))\n');
  fs.writeFileSync(schematic, '(kicad_sch (version 20260128))\n');
  fs.writeFileSync(kicad, `#!${process.execPath}
import fs from 'node:fs';
import path from 'node:path';
const mode = ${JSON.stringify(mode)}, destination = ${JSON.stringify(output)};
const args = process.argv.slice(2);
if (args[0] === 'version') { if (mode === 'version-fail') process.exit(9); console.log('10.0.0-protocol-fixture'); process.exit(0); }
const out = args[args.indexOf('--output') + 1];
if (!out || !process.env.KICAD_CONFIG_HOME) process.exit(11);
if (mode === 'mutate-source') fs.appendFileSync(args.at(-1), '\\nmodified');
if (mode === 'buffer') { process.stdout.write('x'.repeat(3 * 1024 * 1024)); setInterval(() => {}, 1000); }
else if (mode === 'signal') process.kill(process.pid, 'SIGTERM');
else if (mode === 'fail-front' && args[2] === 'svg') { console.error('Fixture native export error'); process.exit(7); }
else if (mode === 'empty') process.exit(0);
else if (mode === 'symlink' && args[2] === 'pos') fs.symlinkSync(args.at(-1), out);
else if (args[2] === 'svg') { if (!out.endsWith('.svg')) process.exit(12); fs.mkdirSync(path.dirname(out), {recursive:true}); fs.writeFileSync(out, '<svg xmlns="http://www.w3.org/2000/svg"><text>Native protocol fixture</text></svg>'); }
else fs.writeFileSync(out, args[2] === 'pos' ? 'Ref,PosX,PosY,Rot,Side\\nR1,1,2,0,top\\n' : 'Reference,Value,Footprint,Datasheet\\nR1,10k,R_0603,\\n');
if (mode === 'race-output') { fs.mkdirSync(destination, {recursive:true}); fs.writeFileSync(path.join(destination, 'keep.txt'), 'last good'); }
console.log('Fixture protocol command complete');
`, { mode: 0o755 });
  return { root, board, schematic, output, kicad };
}

test('protocol: executable discovery validates its actual version and explicit absence does not fall back', t => {
  const f = setup(t);
  const tool = findKicad(f.kicad);
  assert.deepEqual(tool, { available: true, bin: fs.realpathSync(f.kicad), version: '10.0.0-protocol-fixture' });
  assert.equal(findKicad(path.join(f.root, 'does-not-exist')).available, false);
  assert.equal(findKicad('').available, false);
});

test('protocol: successful argument-array exports preserve source bytes and bind every produced file', t => {
  const f = setup(t);
  const before = [fs.readFileSync(f.board), fs.readFileSync(f.schematic)];
  const result = exportKicad(f);
  assert.equal(result.status, 'pass'); assert.equal(result.jobs.length, 4);
  assert.equal(result.jobs.every(j => j.exitCode === 0), true);
  assert.equal(result.sources.every(s => s.unchanged), true);
  assert.equal(result.scope.canonicalNormalization, 'not_performed');
  assert.equal(result.scope.designRuleCheck, 'not_run'); assert.equal(result.scope.electricalRuleCheck, 'not_run');
  assert.equal(result.scope.electricalQualification, 'not_assessed');
  assert.ok(result.jobs[0].args.includes('--use-drill-file-origin'));
  assert.equal(result.jobs[0].args.at(-1), fs.realpathSync(f.board), 'shell metacharacters are passed in one literal argument');
  assert.deepEqual([fs.readFileSync(f.board), fs.readFileSync(f.schematic)], before);
  assert.ok(fs.existsSync(result.receiptPath));
  for (const file of result.files) {
    const bytes = fs.readFileSync(path.join(result.output, file.path));
    assert.equal(hash(bytes), file.sha256); assert.equal(bytes.length, file.bytes);
  }
  assert.deepEqual(result.files.map(f => f.path).sort(), ['artifacts/back/board.svg', 'artifacts/bom.csv', 'artifacts/front/board.svg', 'artifacts/positions.csv']);
});

test('protocol: board-only export omits the schematic job and revalidates supplied discovery metadata', t => {
  const f = setup(t);
  const result = exportKicad({ board: f.board, output: f.output, kicad: { bin: f.kicad, version: 'forged', available: true } });
  assert.equal(result.jobs.length, 3); assert.equal(result.sources.length, 1);
  assert.equal(result.tool.version, '10.0.0-protocol-fixture');
});

test('protocol: missing or failed KiCad gives a clear optional-capability failure', t => {
  const f = setup(t, 'version-fail');
  assert.throws(() => exportKicad(f), e => e instanceof KicadError && e.code === 'KICAD_UNAVAILABLE');
  assert.equal(fs.existsSync(f.output), false);
});

test('protocol: source validation rejects invalid extension, missing file, directories, symlinks and empty data', t => {
  const f = setup(t);
  const linked = path.join(f.root, 'linked.kicad_pcb'); fs.symlinkSync(f.board, linked);
  const empty = path.join(f.root, 'empty.kicad_pcb'); fs.writeFileSync(empty, '');
  const directory = path.join(f.root, 'directory.kicad_pcb'); fs.mkdirSync(directory);
  for (const board of [linked, empty, directory, f.schematic, path.join(f.root, 'absent.kicad_pcb')]) {
    assert.throws(() => exportKicad({ ...f, board }), e => e instanceof KicadError);
    assert.equal(fs.existsSync(f.output), false);
  }
});

test('protocol: nonzero native failure retains actual exit evidence and never publishes partial exports', t => {
  const f = setup(t, 'fail-front');
  let failure;
  assert.throws(() => exportKicad(f), e => { failure = e; return e.code === 'KICAD_EXPORT_FAILED'; });
  assert.equal(fs.existsSync(f.output), false);
  assert.equal(failure.details.receipt.jobs[0].exitCode, 0);
  assert.equal(failure.details.receipt.jobs[1].exitCode, 7);
  assert.match(failure.details.receipt.jobs[1].stderr, /Fixture native export error/);
  assert.equal(failure.details.receipt.status, 'fail');
  assert.ok(fs.existsSync(failure.details.receiptPath));
});

test('protocol: successful exit without exports and generated symlinks are rejected', t => {
  for (const [mode, code] of [['empty', 'NATIVE_EXPORT_MISSING'], ['symlink', 'UNSAFE_NATIVE_OUTPUT']]) {
    const f = setup(t, mode);
    assert.throws(() => exportKicad(f), e => e.code === code);
    assert.equal(fs.existsSync(f.output), false);
  }
});

test('protocol: populated, empty and concurrently created output destinations are never overwritten', t => {
  const f = setup(t); fs.mkdirSync(f.output);
  assert.throws(() => exportKicad(f), e => e.code === 'OUTPUT_EXISTS');
  fs.writeFileSync(path.join(f.output, 'keep.txt'), 'last good');
  assert.throws(() => exportKicad(f), e => e.code === 'OUTPUT_EXISTS');
  assert.equal(fs.readFileSync(path.join(f.output, 'keep.txt'), 'utf8'), 'last good');
  const race = setup(t, 'race-output');
  assert.throws(() => exportKicad(race), e => e.code === 'OUTPUT_EXISTS');
  assert.equal(fs.readFileSync(path.join(race.output, 'keep.txt'), 'utf8'), 'last good');
});

test('protocol: a source changed by the invoked executable is detected and never silently restored', t => {
  const f = setup(t, 'mutate-source');
  assert.throws(() => exportKicad(f), e => e.code === 'KICAD_SOURCE_CHANGED' && e.details.receipt.sources.some(s => !s.unchanged));
  assert.equal(fs.existsSync(f.output), false);
  assert.match(fs.readFileSync(f.board, 'utf8'), /modified/);
});

test('protocol: output buffering and process signals cannot become successful exports', t => {
  for (const mode of ['buffer', 'signal']) {
    const f = setup(t, mode);
    assert.throws(() => exportKicad(f), e => e.code === 'KICAD_EXPORT_FAILED' && e.details.receipt.jobs[0].exitCode !== 0);
    assert.equal(fs.existsSync(f.output), false);
  }
});
