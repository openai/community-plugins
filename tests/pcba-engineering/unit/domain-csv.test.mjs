import test from 'node:test';
import assert from 'node:assert/strict';
import { parseCsv, parseBomCsv, parsePlacementCsv, emitBomCsv, emitPlacementsCsv, DomainError } from '../../../plugins/pcba-engineering/lib/csv.mjs';
const rejects = (fn, code) => assert.throws(fn, e => e instanceof DomainError && e.code === code);

test('RFC 4180 parser handles BOM, escaped quotes, embedded newlines and grouped references', () => {
  const rows = parseBomCsv('\uFEFFRef,PartId,Value,Package,Category,Populated,MPN\r\n"R1, R2; R3",P1,"quoted ""description""\r\nnext line",R_0603,smt,true,\r\n');
  assert.deepEqual(rows.map(r => r.ref), ['R1', 'R2', 'R3']);
  assert.equal(rows[0].value, 'quoted "description"\r\nnext line'); assert.equal(rows[0].sourceRow, 2);
  assert.equal(rows[0].mpn, null);
});

test('explicit mappings resolve ambiguous columns without guessing values', () => {
  const text = 'Ref,Designator,Item,Land,Type,DNP\nR1,R9,resistor,0603,smt,no\n';
  rejects(() => parseBomCsv(text), 'AMBIGUOUS_MAPPING');
  const result = parseBomCsv(text, { ref: 'Designator', partId: 'Item', package: 'Land', category: 'Type' });
  assert.equal(result[0].ref, 'R9'); assert.equal(result[0].populated, true);
  rejects(() => parseBomCsv(text, { ref: 'Not here' }), 'MISSING_MAPPED_COLUMN');
});

test('DNP is explicit, contradictions and missing population are errors', () => {
  const h = 'Ref,PartId,Package,Category';
  rejects(() => parseBomCsv(`${h}\nR1,P,0603,smt\n`), 'MISSING_POPULATION');
  assert.equal(parseBomCsv(`${h},DNP\nR1,P,0603,smt,yes\n`)[0].populated, false);
  rejects(() => parseBomCsv(`${h},DNP,Populated\nR1,P,0603,smt,true,true\n`), 'CONFLICTING_POPULATION');
  rejects(() => parseBomCsv(`${h},Populated\nR1,P,0603,smt,maybe\n`), 'INVALID_BOOLEAN');
});

test('duplicate grouped refs remain available to domain diagnostics', () => {
  const rows = parseBomCsv('Ref,PartId,Package,Category,Populated\n"R1 R1",P,0603,smt,1\n');
  assert.equal(rows.length, 2); assert.equal(rows[0].ref, rows[1].ref);
});

test('inch/mil inputs convert only declared units; no unit guessing', () => {
  const csv = 'Ref,X,Y,Rotation,Side\nR1,1,2,-90,top\n';
  rejects(() => parsePlacementCsv(csv), 'AMBIGUOUS_UNITS');
  const [r] = parsePlacementCsv(csv, { units: 'in' }); assert.equal(r.xMm, 25.4); assert.equal(r.yMm, 50.8); assert.equal(r.rotationDeg, 270);
  assert.equal(parsePlacementCsv(csv, { units: 'mil' })[0].xMm, 0.0254);
  rejects(() => parsePlacementCsv(csv.replace('X,Y', 'X(mm),Y(mm)'), { units: 'in' }), 'CONFLICTING_UNITS');
  rejects(() => parsePlacementCsv(csv.replace('X,Y', 'X(mm),Y(in)')), 'CONFLICTING_UNITS');
});

test('bottom geometry needs a convention and explicit mirror conversion is reversible', () => {
  const csv = 'Ref,X(mm),Y(mm),Rotation,Side\nU1,-5,4,30,B.Cu\n';
  rejects(() => parsePlacementCsv(csv), 'AMBIGUOUS_BOTTOM_CONVENTION');
  const [r] = parsePlacementCsv(csv, { bottomConvention: 'negated-x' });
  assert.equal(r.xMm, 5); assert.equal(r.rotationDeg, 150); assert.equal(r.side, 'bottom');
  const [preserved] = parsePlacementCsv(csv, { bottomConvention: 'top-view' }); assert.equal(preserved.xMm, -5); assert.equal(preserved.rotationDeg, 30);
});

test('canonical exports roundtrip assembly meaning and quote source text safely', () => {
  const pkg = { bom: [{ ref: 'R1', partId: 'r,"1"', value: '10k\nresistor', package: '0603', category: 'smt', populated: false, mpn: null }], placements: [{ ref: 'R1', xMm: 1.23456789, yMm: 2, rotationDeg: 12.5, side: 'bottom', package: '0603' }] };
  const [b] = parseBomCsv(emitBomCsv(pkg)); delete b.sourceRow; assert.deepEqual(b, pkg.bom[0]);
  const [p] = parsePlacementCsv(emitPlacementsCsv(pkg), { bottomConvention: 'top-view' }); delete p.sourceRow; assert.deepEqual(p, pkg.placements[0]);
});

test('malformed CSV is rejected rather than silently shifted or coerced', () => {
  for (const [csv, code] of [
    ['A,A\n1,2\n', 'DUPLICATE_CSV_HEADER'], ['A,B\n1\n', 'CSV_COLUMN_COUNT'], ['A,B\n"x"tail,y', 'INVALID_CSV_QUOTING'],
    ['A,B\n"x,y', 'UNCLOSED_CSV_QUOTE'], ['A,B\nx"y,z', 'INVALID_CSV_QUOTING'], ['', 'EMPTY_CSV'], ['A,\nx,y', 'INVALID_CSV_HEADER'],
    ['A\n"' + 'x'.repeat(8193) + '"', 'CELL_TOO_LARGE'], ['A\n\0', 'INVALID_CSV'],
  ]) rejects(() => parseCsv(csv), code);
  const h = 'Ref,X(mm),Y(mm),Rotation,Side\n';
  for (const n of ['NaN', 'Infinity', '0x20', '1 mm', '']) rejects(() => parsePlacementCsv(`${h}R1,${n},1,0,top\n`), 'INVALID_NUMBER');
});
