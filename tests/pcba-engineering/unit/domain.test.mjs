import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizePackage, checkPackage, parseBomCsv, emitBomCsv, DomainError, TOLERANCES } from '../../../plugins/pcba-engineering/lib/domain.mjs';

function fixture() {
  const row = (ref, category = 'smt', populated = true) => ({ ref, partId: `part-${ref}`, value: '10k', package: 'R_0603', category, populated, mpn: null });
  const placements = [{ ref: 'R1', xMm: 4, yMm: 7, rotationDeg: 0, side: 'top', package: 'R_0603' }, { ref: 'R2', xMm: 10, yMm: 15, rotationDeg: 270, side: 'bottom', package: 'R_0603' }];
  return {
    schemaVersion: 1, board: { id: 'independent-fixture', name: 'Document workflow fixture', revision: 'A', widthMm: 30, heightMm: 20 }, variant: 'default', quantity: 5,
    conventions: { units: 'mm', coordinateFrame: 'board-bottom-left', rotation: 'ccw', bottomView: 'top' },
    bom: [row('R1'), row('R2'), row('C1', 'smt', false), row('J1', 'tht'), row('FID1', 'fiducial'), row('H1', 'mechanical')],
    placements, authoritativePlacements: structuredClone(placements), source: { name: 'source', explicitlySynthetic: true }, visual: { arbitrary: 'preserved' },
  };
}
const codes = p => checkPackage(p).findings.map(f => f.code);
const rejects = (f, code) => assert.throws(f, e => e instanceof DomainError && e.code === code);

test('document success keeps procurement, electrical and authorization unknown', () => {
  const p = fixture();
  const before = structuredClone(p);
  const result = checkPackage(p);
  assert.equal(result.status, 'pass');
  assert.deepEqual(result.counts, { bom: 6, requiredPlacements: 2, actualPlacements: 2, errors: 0, warnings: 1 });
  assert.equal(result.scope.materialReadiness, 'unknown');
  assert.equal(result.scope.manufacturingAuthorization, 'not_assessed');
  assert.equal(result.scope.electricalQualification, 'not_assessed');
  assert.deepEqual(p, before, 'checking must not mutate inputs');
  assert.deepEqual(result, checkPackage(p), 'checking is deterministic');
});

test('normalization clones extras, canonicalizes angles, and retains DNP rows', () => {
  const p = fixture(); p.note = { arbitrary: ['metadata'] }; p.placements[0].rotationDeg = -90; p.placements[1].rotationDeg = 720;
  const n = normalizePackage(p);
  assert.equal(n.placements[0].rotationDeg, 270); assert.equal(n.placements[1].rotationDeg, 0);
  assert.equal(n.bom.find(r => r.ref === 'C1').populated, false);
  n.note.arbitrary.push('new'); assert.deepEqual(p.note.arbitrary, ['metadata']);
});

test('canonical references reject group delimiters before checks or export', () => {
  for (const ref of ['R1,R2', 'R1;R2']) {
    for (const collection of ['bom', 'placements', 'authoritativePlacements']) {
      const p = fixture(); p[collection][0].ref = ref;
      rejects(() => normalizePackage(p), 'INVALID_REFERENCE');
      rejects(() => checkPackage(p), 'INVALID_REFERENCE');
    }
    const p = fixture();
    p.sourceFindings = [{ id: 'source-note', severity: 'warning', message: 'Review source.', refs: [ref] }];
    rejects(() => normalizePackage(p), 'INVALID_REFERENCE');
  }
});

test('grouped BOM CSV references still normalize and roundtrip as individual references', () => {
  for (const refs of ['R1,R2', 'R1;R2', 'R1 R2']) {
    const p = fixture();
    p.bom = parseBomCsv(`Ref,PartId,Value,Package,Category,Populated,MPN\n"${refs}",resistor,10k,R_0603,smt,true,\n`);
    const normalized = normalizePackage(p);
    assert.deepEqual(normalized.bom.map(row => row.ref), ['R1', 'R2']);
    assert.equal(checkPackage(normalized).status, 'pass');
    assert.deepEqual(parseBomCsv(emitBomCsv(normalized)).map(row => row.ref), ['R1', 'R2']);
  }
});

test('missing SMT row fails and cannot disappear by deleting its BOM row too', () => {
  const p = fixture(); p.placements.shift();
  assert.ok(codes(p).includes('MISSING_PLACEMENT'));
  p.bom.shift();
  assert.ok(codes(p).includes('SOURCE_BOM_MISSING_REFERENCE'));
});

test('DNP placement fails while fiducial and mechanical positions do not inflate SMT counts', () => {
  const p = fixture(); delete p.authoritativePlacements;
  p.placements.push({ ref: 'C1', xMm: 3, yMm: 3, rotationDeg: 0, side: 'top' });
  assert.ok(codes(p).includes('DNP_PLACEMENT'));
  p.placements.pop();
  p.placements.push({ ref: 'FID1', xMm: 3, yMm: 3, rotationDeg: 0, side: 'top' }, { ref: 'H1', xMm: 0, yMm: 0, rotationDeg: 0, side: 'top' });
  assert.equal(checkPackage(p).status, 'pass');
  assert.equal(checkPackage(p).counts.actualPlacements, 2);
  assert.equal(checkPackage(p).counts.requiredPlacements, 2);
});

test('case-insensitive duplicate refs and unmatched placements are errors', () => {
  const p = fixture(); p.bom.push({ ...p.bom[0], ref: 'r1' }); p.placements.push({ ...p.placements[0], ref: 'r1' }, { ...p.placements[0], ref: 'X1' });
  for (const code of ['DUPLICATE_BOM_REFERENCE', 'DUPLICATE_PLACEMENT_REFERENCE', 'EXTRA_PLACEMENT', 'PLACEMENT_NOT_IN_AUTHORITY']) assert.ok(codes(p).includes(code), code);
});

test('authoritative coordinates, side and rotation are checked independently', () => {
  const p = fixture(); p.placements[0].xMm += 0.1; p.placements[0].side = 'bottom'; p.placements[1].rotationDeg = 90;
  for (const code of ['POSITION_MISMATCH', 'SIDE_MISMATCH', 'ROTATION_MISMATCH']) assert.ok(codes(p).includes(code), code);
});

test('position tolerance is radial and angle difference wraps around zero', () => {
  const p = fixture(); p.placements[0].xMm += TOLERANCES.positionMm; p.placements[0].rotationDeg = 359.995;
  assert.equal(checkPackage(p).status, 'pass');
  p.placements[0].yMm += TOLERANCES.positionMm;
  assert.ok(codes(p).includes('POSITION_MISMATCH'));
});

test('bounds check works without an authoritative source and declares its limit', () => {
  const p = fixture(); delete p.authoritativePlacements;
  const report = checkPackage(p);
  assert.equal(report.status, 'pass'); assert.equal(report.scope.geometryVerification, 'bounds-only');
  assert.ok(codes(p).includes('AUTHORITATIVE_GEOMETRY_UNAVAILABLE'));
  p.placements[0].xMm = -0.1; assert.ok(codes(p).includes('PLACEMENT_OUTSIDE_BOARD'));
});

test('supplying an empty or duplicated source is not treated as missing independent evidence', () => {
  const p = fixture(); p.authoritativePlacements = [];
  assert.ok(codes(p).includes('MISSING_AUTHORITATIVE_PLACEMENT'));
  p.authoritativePlacements = [p.placements[0], p.placements[0], p.placements[1]];
  assert.ok(codes(p).includes('DUPLICATE_AUTHORITATIVE_REFERENCE'));
});

test('BOM/source package mismatch is detected even when export package column is absent', () => {
  const p = fixture(); p.bom[0].package = 'R_0805'; delete p.placements[0].package;
  assert.ok(codes(p).includes('BOM_SOURCE_PACKAGE_MISMATCH'));
});

test('unknown part identities and source warnings remain visible but do not imply physical readiness', () => {
  const p = fixture(); p.bom[0].partId = null; p.bom[0].mpn = null;
  p.sourceFindings = [{ id: 'datasheet-gap', severity: 'warning', message: 'Source has no temperature rating.', refs: ['R1'] }];
  const report = checkPackage(p);
  assert.equal(report.status, 'pass');
  assert.ok(report.findings.some(f => f.code === 'PART_METADATA_INCOMPLETE'));
  assert.ok(report.findings.some(f => f.sourceFindingId === 'datasheet-gap'));
  p.sourceFindings[0].severity = 'error'; assert.equal(checkPackage(p).status, 'fail');
});

test('invalid numeric state, conventions and category fail deterministically', () => {
  for (const [change, code] of [
    [p => p.quantity = 1.5, 'INVALID_NUMBER'], [p => p.quantity = 0, 'INVALID_NUMBER'], [p => p.board.widthMm = 0, 'INVALID_NUMBER'],
    [p => p.placements[0].xMm = Infinity, 'INVALID_NUMBER'], [p => p.placements[0].xMm = '5', 'INVALID_NUMBER'],
    [p => p.conventions.units = 'in', 'AMBIGUOUS_CONVENTIONS'], [p => delete p.conventions, 'INVALID_OBJECT'],
    [p => p.bom[0].populated = 'yes', 'INVALID_POPULATION'], [p => p.bom[0].category = 'auto', 'INVALID_CATEGORY'],
    [p => p.bom[0].ref = 'R 1', 'INVALID_REFERENCE'], [p => p.schemaVersion = 2, 'UNSUPPORTED_SCHEMA'],
    [p => p.bom = [], 'EMPTY_BOM'], [p => p.source = [], 'INVALID_OBJECT'],
  ]) { const p = fixture(); change(p); rejects(() => normalizePackage(p), code); }
});

test('non-JSON and prototype pollution inputs are rejected without invoking accessors', () => {
  for (const value of [new Date(), () => {}, undefined, 5n]) { const p = fixture(); p.extra = value; rejects(() => normalizePackage(p), 'INVALID_OBJECT'); }
  const p = fixture(); p.extra = JSON.parse('{"__proto__":{"polluted":true}}');
  rejects(() => normalizePackage(p), 'UNSAFE_OBJECT_KEY'); assert.equal({}.polluted, undefined);
  const q = fixture(); let accessed = false; Object.defineProperty(q, 'evil', { enumerable: true, get() { accessed = true; return 'value'; } });
  rejects(() => normalizePackage(q), 'INVALID_OBJECT'); assert.equal(accessed, false);
  const cyc = fixture(); cyc.loop = cyc; rejects(() => normalizePackage(cyc), 'INPUT_TOO_LARGE');
});

test('security-looking source strings remain inert, preserved data', () => {
  const p = fixture(); p.bom[0].value = '<img src=x onerror="globalThis.__pcbaTest=true">'; p.source.notes = '$(touch /tmp/pcba-injection); `eval`';
  const n = normalizePackage(p); assert.equal(n.bom[0].value, p.bom[0].value); assert.equal(globalThis.__pcbaTest, undefined); assert.equal(checkPackage(n).status, 'pass');
});
