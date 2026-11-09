/** Canonical, deterministic assembly-package checks. No I/O or machine integration. */
import { DomainError } from './csv.mjs';
export { DomainError, parseBomCsv, parsePlacementCsv, emitBomCsv, emitPlacementsCsv } from './csv.mjs';

export const TOLERANCES = Object.freeze({ positionMm: 0.001, rotationDeg: 0.01 });
const LIMIT = 100_000;
const fail = (code, message, path) => { throw new DomainError(code, message, { path }); };
function object(v, path) {
  if (!v || typeof v !== 'object' || Array.isArray(v) || ![Object.prototype, null].includes(Object.getPrototypeOf(v))) fail('INVALID_OBJECT', `${path} must be a plain object.`, path);
  return v;
}
function text(v, path, { empty = false, max = 8192 } = {}) {
  if (typeof v !== 'string' || v.length > max || v.includes('\0') || (!empty && !v.trim())) fail('INVALID_STRING', `${path} must be ${empty ? 'a' : 'a nonempty'} bounded string.`, path);
  return v.trim();
}
function ref(v, path) {
  const s = text(v, path, { max: 256 });
  if (/[\s,;]/.test(s)) fail('INVALID_REFERENCE', `${path} must identify one reference without whitespace, commas or semicolons. Grouped references are supported only in BOM CSV input.`, path);
  return s;
}
function number(v, path, { positive = false, integer = false, max = 1e6 } = {}) {
  if (typeof v !== 'number' || !Number.isFinite(v) || Math.abs(v) > max || (positive && v <= 0) || (integer && !Number.isSafeInteger(v))) fail('INVALID_NUMBER', `${path} must be a supported finite ${integer ? 'integer' : 'number'}${positive ? ' greater than zero' : ''}.`, path);
  return Object.is(v, -0) ? 0 : v;
}
function list(v, path) {
  if (!Array.isArray(v) || v.length > LIMIT) fail('INVALID_ARRAY', `${path} must be an array of at most ${LIMIT} entries.`, path);
  return v;
}
/** Reject executable/non-JSON values and hostile object keys before cloning metadata. */
function jsonClone(v, path = '$', depth = 0, budget = { n: 0, bytes: 0 }) {
  if (depth > 40 || ++budget.n > 2_000_000) fail('INPUT_TOO_LARGE', 'Input nesting or member count exceeds the supported limit.', path);
  if (v === null || typeof v === 'boolean') return v;
  if (typeof v === 'number') return number(v, path, { max: Number.MAX_VALUE });
  if (typeof v === 'string') {
    if (v.length > 4 * 1024 * 1024 || v.includes('\0')) fail('INVALID_STRING', 'Input text exceeds limit or contains NUL.', path);
    budget.bytes += Buffer.byteLength(v, 'utf8');
    if (budget.bytes > 16 * 1024 * 1024) fail('INPUT_TOO_LARGE', 'Aggregate input text exceeds 16 MiB.', path);
    return v;
  }
  if (Array.isArray(v)) return list(v, path).map((x, i) => jsonClone(x, `${path}[${i}]`, depth + 1, budget));
  object(v, path);
  const out = {};
  if (Object.getOwnPropertySymbols(v).length) fail('INVALID_OBJECT', 'Symbol-keyed metadata is not JSON data.', path);
  for (const [k, descriptor] of Object.entries(Object.getOwnPropertyDescriptors(v))) {
    if (['__proto__', 'prototype', 'constructor'].includes(k)) fail('UNSAFE_OBJECT_KEY', 'Reserved object keys are not accepted.', `${path}.${k}`);
    if (descriptor.get || descriptor.set || !descriptor.enumerable) fail('INVALID_OBJECT', 'Accessor or non-enumerable metadata is not JSON data.', `${path}.${k}`);
    out[k] = jsonClone(descriptor.value, `${path}.${k}`, depth + 1, budget);
  }
  return out;
}
function normalizePlacement(v, path) {
  object(v, path);
  v.ref = ref(v.ref, `${path}.ref`);
  v.xMm = number(v.xMm, `${path}.xMm`);
  v.yMm = number(v.yMm, `${path}.yMm`);
  const a = number(v.rotationDeg, `${path}.rotationDeg`);
  v.rotationDeg = a % 360 < 0 ? a % 360 + 360 : (a % 360 || 0);
  if (!['top', 'bottom'].includes(v.side)) fail('INVALID_SIDE', `${path}.side must be top or bottom.`, `${path}.side`);
  if (v.package !== undefined) v.package = text(v.package, `${path}.package`, { empty: true });
  return v;
}

export function normalizePackage(input) {
  const p = jsonClone(input);
  object(p, '$');
  if (p.schemaVersion !== 1) fail('UNSUPPORTED_SCHEMA', 'schemaVersion must be 1.', '$.schemaVersion');
  object(p.board, '$.board');
  for (const key of ['id', 'name', 'revision']) p.board[key] = text(p.board[key], `$.board.${key}`, { max: 256 });
  for (const key of ['widthMm', 'heightMm']) p.board[key] = number(p.board[key], `$.board.${key}`, { positive: true, max: 10_000 });
  p.variant = text(p.variant, '$.variant', { max: 256 });
  p.quantity = number(p.quantity, '$.quantity', { integer: true, positive: true });
  if (p.source !== undefined) object(p.source, '$.source');
  if (p.visual !== undefined) object(p.visual, '$.visual');
  object(p.conventions, '$.conventions');
  const conventions = { units: 'mm', coordinateFrame: 'board-bottom-left', rotation: 'ccw', bottomView: 'top' };
  for (const [key, value] of Object.entries(conventions)) if (p.conventions[key] !== value) fail('AMBIGUOUS_CONVENTIONS', `Canonical conventions.${key} must explicitly be ${value}.`, `$.conventions.${key}`);
  p.bom = list(p.bom, '$.bom').map((r, i) => {
    const path = `$.bom[${i}]`; object(r, path);
    r.ref = ref(r.ref, `${path}.ref`);
    for (const key of ['partId', 'value', 'package']) r[key] = r[key] == null ? '' : text(r[key], `${path}.${key}`, { empty: true });
    if (!['smt', 'tht', 'mechanical', 'fiducial'].includes(r.category)) fail('INVALID_CATEGORY', `${path}.category must explicitly describe the assembly operation.`, `${path}.category`);
    if (typeof r.populated !== 'boolean') fail('INVALID_POPULATION', `${path}.populated must be a boolean.`, `${path}.populated`);
    r.mpn = r.mpn == null || r.mpn === '' ? null : text(r.mpn, `${path}.mpn`);
    return r;
  });
  if (!p.bom.length) fail('EMPTY_BOM', 'An assembly package requires at least one BOM reference.', '$.bom');
  p.placements = list(p.placements, '$.placements').map((v, i) => normalizePlacement(v, `$.placements[${i}]`));
  if (p.authoritativePlacements !== undefined) p.authoritativePlacements = list(p.authoritativePlacements, '$.authoritativePlacements').map((v, i) => normalizePlacement(v, `$.authoritativePlacements[${i}]`));
  if (p.sourceFindings !== undefined) p.sourceFindings = list(p.sourceFindings, '$.sourceFindings').map((r, i) => {
    const path = `$.sourceFindings[${i}]`; object(r, path);
    r.id = text(r.id, `${path}.id`, { max: 256 }); r.message = text(r.message, `${path}.message`);
    if (!['warning', 'error'].includes(r.severity)) fail('INVALID_SEVERITY', `${path}.severity must be warning or error.`, `${path}.severity`);
    if (r.refs !== undefined) r.refs = list(r.refs, `${path}.refs`).map((v, n) => ref(v, `${path}.refs[${n}]`));
    return r;
  });
  return p;
}

export function checkPackage(input) {
  const p = normalizePackage(input);
  const findings = [];
  const add = (code, severity, refs, message, detail = {}) => findings.push({ id: `${code}:${findings.length + 1}`, code, severity, refs, message, ...detail });
  const key = r => r.ref.toUpperCase();
  function index(rows, kind) {
    const map = new Map();
    for (const row of rows) {
      if (map.has(key(row))) add(`DUPLICATE_${kind}_REFERENCE`, 'error', [row.ref], `${kind.toLowerCase()} contains a repeated reference (case-insensitive).`);
      else map.set(key(row), row);
    }
    return map;
  }
  const bom = index(p.bom, 'BOM'), placement = index(p.placements, 'PLACEMENT');
  const auth = p.authoritativePlacements === undefined ? null : index(p.authoritativePlacements, 'AUTHORITATIVE');
  const required = [...bom.values()].filter(r => r.category === 'smt' && r.populated);
  const missing = required.filter(r => !placement.has(key(r)));
  for (const r of missing) add('MISSING_PLACEMENT', 'error', [r.ref], `Required populated SMT reference ${r.ref} is absent from the supplied placement export.`, { expected: 'placement row', observed: 'absent' });
  for (const r of p.placements) {
    const b = bom.get(key(r));
    if (!b) add('EXTRA_PLACEMENT', 'error', [r.ref], `Placement reference ${r.ref} has no corresponding BOM entry.`);
    else {
      if (['smt', 'tht'].includes(b.category) && !b.populated) add('DNP_PLACEMENT', 'error', [r.ref], `Reference ${r.ref} is excluded from this population but remains in the assembly placement export.`);
      if (b.package && r.package && b.package !== r.package) add('PACKAGE_MISMATCH', 'error', [r.ref], `Package differs between BOM and placement export for ${r.ref}.`, { expected: b.package, observed: r.package });
    }
    if (r.xMm < -TOLERANCES.positionMm || r.yMm < -TOLERANCES.positionMm || r.xMm > p.board.widthMm + TOLERANCES.positionMm || r.yMm > p.board.heightMm + TOLERANCES.positionMm) {
      add('PLACEMENT_OUTSIDE_BOARD', 'error', [r.ref], `Placement ${r.ref} lies outside the declared rectangular board bounds.`, { expected: { xMm: [0, p.board.widthMm], yMm: [0, p.board.heightMm] }, observed: { xMm: r.xMm, yMm: r.yMm } });
    }
    if (auth) {
      const a = auth.get(key(r));
      if (!a) add('PLACEMENT_NOT_IN_AUTHORITY', 'error', [r.ref], `Placement ${r.ref} is absent from the supplied authoritative geometry.`);
      else {
        if (a.side !== r.side) add('SIDE_MISMATCH', 'error', [r.ref], `Board side differs from authoritative geometry for ${r.ref}.`, { expected: a.side, observed: r.side });
        const dx = r.xMm - a.xMm, dy = r.yMm - a.yMm;
        if (Math.hypot(dx, dy) > TOLERANCES.positionMm + 1e-12) add('POSITION_MISMATCH', 'error', [r.ref], `Coordinates differ from authoritative geometry for ${r.ref}.`, { expected: { xMm: a.xMm, yMm: a.yMm }, observed: { xMm: r.xMm, yMm: r.yMm } });
        const rotationError = Math.abs(((r.rotationDeg - a.rotationDeg + 540) % 360) - 180);
        if (rotationError > TOLERANCES.rotationDeg + 1e-12) add('ROTATION_MISMATCH', 'error', [r.ref], `Rotation differs from authoritative geometry for ${r.ref}.`, { expected: a.rotationDeg, observed: r.rotationDeg });
        if (a.package && r.package && a.package !== r.package) add('AUTHORITATIVE_PACKAGE_MISMATCH', 'error', [r.ref], `Package differs from authoritative geometry for ${r.ref}.`, { expected: a.package, observed: r.package });
      }
    }
  }
  if (auth) {
    for (const r of required) if (!auth.has(key(r))) add('MISSING_AUTHORITATIVE_PLACEMENT', 'error', [r.ref], `Required reference ${r.ref} lacks authoritative placement geometry; its coordinates cannot be reconstructed from a BOM.`);
    for (const a of auth.values()) {
      const b = bom.get(key(a));
      if (!b) add('SOURCE_BOM_MISSING_REFERENCE', 'error', [a.ref], `Authoritative source reference ${a.ref} is absent from the BOM. Keep excluded references visible with populated=false.`);
      else if (a.package && b.package && a.package !== b.package) add('BOM_SOURCE_PACKAGE_MISMATCH', 'error', [a.ref], `BOM package differs from authoritative geometry for ${a.ref}.`, { expected: a.package, observed: b.package });
    }
  } else add('AUTHORITATIVE_GEOMETRY_UNAVAILABLE', 'warning', [], 'No authoritative placement geometry was supplied. Reference reconciliation and declared bounds are checked; exact coordinates, orientation and side are not independently verified.');
  const missingMpn = [...bom.values()].filter(r => ['smt', 'tht'].includes(r.category) && r.populated && !r.mpn).map(r => r.ref);
  if (missingMpn.length) add('MPN_UNKNOWN', 'warning', missingMpn, 'Manufacturer part numbers are missing for some populated parts. Procurement identity remains unresolved; values and footprints do not prove electrical equivalence.');
  const incomplete = [...bom.values()].filter(r => r.category === 'smt' && r.populated && (!r.partId || !r.package)).map(r => r.ref);
  if (incomplete.length) add('PART_METADATA_INCOMPLETE', 'warning', incomplete, 'Some populated SMT references have no source part identity or package metadata. Record this gap before engineering/manufacturing review.');
  for (const f of p.sourceFindings ?? []) add('SOURCE_FINDING', f.severity, f.refs ?? [], f.message, { sourceFindingId: f.id });
  const errors = findings.filter(f => f.severity === 'error').length;
  const warnings = findings.filter(f => f.severity === 'warning').length;
  const status = errors ? 'fail' : 'pass';
  return {
    schemaVersion: 1, status,
    board: { id: p.board.id, name: p.board.name, revision: p.board.revision }, variant: p.variant, quantity: p.quantity,
    counts: { bom: p.bom.length, requiredPlacements: required.length, actualPlacements: p.placements.filter(r => bom.get(key(r))?.category === 'smt' && bom.get(key(r))?.populated).length, errors, warnings },
    tolerances: { ...TOLERANCES }, findings,
    scope: { documentConsistency: status, materialReadiness: 'unknown', electricalQualification: 'not_assessed', manufacturingAuthorization: 'not_assessed', geometryVerification: auth ? 'supplied-authoritative-comparison' : 'bounds-only', geometryProvenance: 'supplied-source-not-authenticated' },
  };
}
