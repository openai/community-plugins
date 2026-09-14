import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { prepareJob, parseBom, parseCsv, validateJob, DomainError, digest, importCanonicalJob, exportCanonicalJob } from '../../plugins/openpnp/mcp/domain/index.mjs';

const fixture = name => readFile(new URL(`../../fixtures/openpnp/${name}`, import.meta.url), 'utf8');
const ref = await fixture('reference.csv'), bom = await fixture('bom.csv');
const prepare = overrides => prepareJob({ format: 'reference-csv', content: ref, bomContent: bom, widthMm: 40, heightMm: 30, variant: 'production', ...overrides });
const code = expected => e => e instanceof DomainError && e.code === expected;
const diagnostic = expected => e => e instanceof DomainError && e.details.diagnostics?.some(d => d.code === expected);

test('BOM, DNP, variant, exact source receipts and metadata survive atomic import', () => {
  const job = prepare();
  assert.equal(job.sources[0].sha256, createHash('sha256').update(ref).digest('hex'));
  assert.equal(job.sources[0].bytes, Buffer.byteLength(ref));
  assert.equal(job.sources[1].sha256, createHash('sha256').update(bom).digest('hex'));
  assert.deepEqual(job.parts, [{ id: 'RES-10K', packageId: 'R_0603', value: '10k', heightMm: 0.5 }, { id: 'CAP-4U7', packageId: 'C_0603', value: '4.7u', heightMm: 0.8 }]);
  assert.deepEqual(job.boards[0].placements.map(p => [p.ref, p.x, p.y, p.rotation, p.side, p.enabled]), [
    ['R1', 10, 5, 90, 'top', true], ['R2', 20, 5, 90, 'top', false], ['C1', 7, 4, 90, 'bottom', true],
  ]);
  assert.equal(job.boards[0].placements[0].metadata.Note, 'qualified, sample');
  assert.equal(job.boards[0].placements[1].metadata.Note, 'DNP with "quoted" note');
  assert.equal(prepare().revision, job.revision);
  assert.equal(validateJob(job).valid, true);
  assert.equal(validateJob(job).productionQualified, false);
});

test('grouped BOM refs expand and duplicate identities are diagnosed', () => {
  assert.deepEqual(parseBom(bom).records.map(p => p.ref), ['R1', 'R2', 'C1']);
  assert.throws(() => parseBom('Refs,PartId\n"R1,R2",P\nR2,P\n'), diagnostic('duplicate_reference'));
});

test('physical CSV source lines survive CRLF and quoted multiline values', () => {
  assert.deepEqual(parseCsv('A,B\r\n"one\ntwo","say ""hi"""\r\nlast,value\r\n'), [
    { row: 1, cells: ['A', 'B'] }, { row: 2, cells: ['one\ntwo', 'say "hi"'] }, { row: 4, cells: ['last', 'value'] },
  ]);
  for (const source of ['a,b\n"unclosed', 'a,b\n"x"tail,z', 'a,b\na"b,c']) assert.throws(() => parseCsv(source), code('malformed_csv'));
});

test('malformed numeric, side and duplicate rows are rejected together with original lines', async () => {
  try { prepare({ content: await fixture('invalid-reference.csv'), bomContent: undefined }); assert.fail('Import must fail'); }
  catch (e) {
    assert.equal(e.code, 'import_invalid');
    assert.deepEqual(e.details.diagnostics.map(d => [d.code, d.row]), [['invalid_number', 2], ['invalid_side', 3], ['duplicate_reference', 5]]);
  }
});

test('length units normalize exactly, angle degrees remain unchanged', () => {
  const source = 'Ref,Value,Package,X (in),Y (mil),Rotation,Side,Height (mm)\nU1,IC,QFN,1,1000,-450,top,1\n';
  const p = prepare({ content: source, bomContent: undefined }).boards[0].placements[0];
  assert.equal(p.x, 25.4); assert.equal(p.y, 25.4); assert.equal(p.heightMm, 1); assert.equal(p.rotation, -90);
  assert.throws(() => prepare({ content: source.replace(',1,1000,', ',1mm,1000,'), bomContent: undefined }), diagnostic('conflicting_units'));
  assert.throws(() => prepare({ content: source.replace('X (in)', 'X').replace('Y (mil)', 'Y'), bomContent: undefined }), diagnostic('missing_units'));
});

test('KiCad legacy bottom conversion is explicit and recognizes the actual units header', async () => {
  const content = await fixture('kicad-legacy.pos');
  assert.throws(() => prepareJob({ format: 'kicad-pos', content }), diagnostic('ambiguous_bottom_convention'));
  const job = prepareJob({ format: 'kicad-pos', content, kicadBottomConvention: 'negated-x', widthMm: 40, heightMm: 30 });
  assert.deepEqual(job.boards[0].placements.map(p => [p.ref, p.x, p.y, p.rotation, p.side]), [['R1', 10, 5, 0, 'top'], ['C1', 7, 4, 135, 'bottom']]);
  assert.throws(() => prepareJob({ format: 'kicad-pos', content, units: 'in' }), code('conflicting_units'));
  const normalized = prepareJob({ format: 'kicad-pos', content: content.replace('-7.0000', '7.0000'), kicadBottomConvention: 'top-view' });
  assert.equal(normalized.boards[0].placements[1].rotation, 45);
});

test('missing BOM coverage, conflicting package/value/height and variant ambiguity never silently disappear', () => {
  assert.throws(() => prepare({ variant: undefined }), diagnostic('variant_required'));
  assert.throws(() => prepare({ bomContent: bom.replace('"R1,R2"', 'R1') }), diagnostic('bom_reference_missing'));
  assert.throws(() => prepare({ bomContent: bom + 'R9,P,R_0603,x,0.5,false,\n' }), diagnostic('bom_reference_unplaced'));
  assert.throws(() => prepare({ bomContent: bom.replace('C_0603', 'C_0805') }), diagnostic('package_conflict'));
  assert.throws(() => prepare({ bomContent: bom.replace('4.7u', '10u') }), diagnostic('value_conflict'));
  assert.throws(() => prepare({ bomContent: bom.replace('0.8', '0.9') }), diagnostic('height_conflict'));
  const selected = prepare({ variant: 'other' }); assert.equal(selected.boards[0].placements[2].enabled, false);
});

test('ambiguous columns, unsupported native XML, oversized and invalid encodings reject clearly', () => {
  const content = ref.replace('X (mm)', 'X (mm),PosX').replace(/,(10|20|7),/gu, ',$1,$1,');
  assert.throws(() => prepare({ content }), code('ambiguous_column'));
  assert.equal(prepare({ content, mapping: { x: 'X (mm)' } }).boards[0].placements[0].metadata.PosX, '10');
  assert.throws(() => prepareJob({ format: 'native-board-xml', content: '<board/>' }), code('unsupported_format'));
  assert.throws(() => parseCsv('a'.repeat(4 * 1024 * 1024 + 1)), code('input_limit'));
  for (const source of ['A\u0000', 'A\uFFFD', 'A\uD800']) assert.throws(() => parseCsv(source), code('invalid_encoding'));
  assert.throws(() => prepare({ widthMm: -1 }), code('invalid_number'));
  assert.throws(() => prepare({ side: 'automatic' }), code('invalid_side'));
});

test('canonical roundtrip preserves semantics and recomputes receipt; malformed objects fail structurally', () => {
  const job = prepare(); assert.deepEqual(importCanonicalJob(exportCanonicalJob(job)), job);
  assert.deepEqual(prepareJob({ format: 'canonical-json', content: exportCanonicalJob(job) }), job);
  assert.throws(() => importCanonicalJob('null'), code('invalid_job'));
  assert.throws(() => importCanonicalJob('{"x":1e999}'), code('invalid_number'));
  assert.equal(validateJob(null).valid, false);
  assert.throws(() => digest({ bad: undefined }), code('invalid_object'));
  const altered = structuredClone(job); altered.parts[0].heightMm = 1;
  assert.throws(() => importCanonicalJob(JSON.stringify(altered)), code('invalid_job'));
});
