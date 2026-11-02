import { DomainError, LIMITS, fail, textInput, sourceReceipt, digest, normalizeAngle, identifier, finite, side } from './common.mjs';

/** Strict bounded RFC-4180-style comma parser. Row means physical source line. */
export function parseCsv(content) {
  const text = textInput(content); const rows = []; let cells = [], field = '', quoted = false, closed = false, line = 1, rowLine = 1;
  const pushField = () => { cells.push(field); field = ''; closed = false; if (cells.length > 256) fail('input_limit', 'Too many CSV columns.', { row: rowLine }); };
  const pushRow = () => { pushField(); if (cells.some(x => x.trim())) rows.push({ row: rowLine, cells }); cells = []; if (rows.length > LIMITS.rows) fail('input_limit', 'Too many CSV rows.'); };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else { quoted = false; closed = true; } }
      else { field += c; if (c === '\n') line++; }
    } else if (c === '"') {
      if (field || closed) fail('malformed_csv', 'Quote in an unquoted field.', { row: line }); quoted = true;
    } else if (c === ',') pushField();
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      pushRow(); line++; rowLine = line;
    } else {
      if (closed) fail('malformed_csv', 'Unexpected text after closing quote.', { row: line }); field += c;
    }
    if (field.length > LIMITS.field) fail('input_limit', 'CSV field exceeds limit.', { row: line });
  }
  if (quoted) fail('malformed_csv', 'Unclosed quoted field.', { row: rowLine });
  if (field || cells.length || closed) pushRow();
  if (!rows.length) fail('empty_import', 'No records found.');
  return rows;
}

const norm = s => s.trim().toLowerCase().replace(/[ _-]/gu, '');
const ALIASES = {
  ref: ['ref', 'refs', 'refdes', 'designator', 'component'], value: ['value', 'val', 'comment', 'compvalue'],
  packageId: ['package', 'packageid', 'footprint', 'pattern', 'comppackage'], partId: ['partid', 'mpn'],
  x: ['x', 'posx', 'refx', 'symx'], y: ['y', 'posy', 'refy', 'symy'], rotation: ['rotation', 'rot', 'rotate', 'symrotate'],
  side: ['side', 'layer', 'tb', 'symmirror'], heightMm: ['height', 'heightmm'],
  dnp: ['dnp', 'dni', 'donotpopulate'], variants: ['variants', 'variant'], type: ['type'],
};
function headerKey(header) { return norm(header.replace(/\s*\((?:mm|mil|in)\)\s*$/iu, '')); }
function headerMap(headers, mapping = {}) {
  if (!mapping || typeof mapping !== 'object' || Array.isArray(mapping)) fail('invalid_mapping', 'Column mapping must be an object.');
  const seen = new Set();
  for (const h of headers) { const key = norm(h); if (!key || seen.has(key)) fail('duplicate_header', 'Empty or duplicate CSV header.', { column: h }); seen.add(key); }
  const out = {};
  for (const [key, aliases] of Object.entries(ALIASES)) {
    if (mapping[key] !== undefined) {
      if (typeof mapping[key] !== 'string' || !headers.includes(mapping[key])) fail('invalid_mapping', `Mapped column for ${key} was not found.`);
      out[key] = headers.indexOf(mapping[key]);
    } else {
      const indices = headers.flatMap((h, i) => aliases.includes(headerKey(h)) ? [i] : []);
      if (indices.length > 1) fail('ambiguous_column', `Multiple columns map to ${key}. Use an explicit mapping.`, { columns: indices.map(i => headers[i]) });
      if (indices.length) out[key] = indices[0];
    }
  }
  for (const key of Object.keys(mapping)) if (!(key in ALIASES)) fail('invalid_mapping', `Unknown mapping field ${key}.`);
  if (new Set(Object.values(out)).size !== Object.values(out).length) fail('invalid_mapping', 'A column cannot supply multiple semantic fields.');
  return out;
}
function required(map, keys) { for (const key of keys) if (map[key] === undefined) fail('missing_column', `Missing required column ${key}.`, { column: key }); }
function bool(raw, label) {
  const s = String(raw ?? '').trim().toLowerCase();
  if (['', 'false', 'no', '0'].includes(s)) return false;
  if (['true', 'yes', '1', 'dnp', 'dni'].includes(s)) return true;
  fail('invalid_boolean', `${label} must be true/false, yes/no or 1/0.`);
}
function importSide(raw) {
  const s = String(raw).trim().toLowerCase();
  if (['top', 't', 'front', 'f.cu'].includes(s)) return 'top';
  if (['bottom', 'b', 'back', 'b.cu'].includes(s)) return 'bottom';
  fail('invalid_side', `Unrecognized board side ${raw}.`);
}
function number(raw, label, { units, header = '', length = false, optional = false } = {}) {
  const s = String(raw ?? '').trim(); if (!s && optional) return null;
  const match = /^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(?:\s*(mm|mil|in))?$/iu.exec(s);
  if (!match) fail('invalid_number', `${label} requires a finite decimal number.`, { column: label });
  const n = Number(match[1]);
  if (!Number.isFinite(n) || Math.abs(n) > 1e9) fail('invalid_number', `${label} is outside the numeric range.`, { column: label });
  if (!length) { if (match[2]) fail('invalid_units', `${label} is an angle in degrees.`); return n; }
  const headerUnit = /\((mm|mil|in)\)\s*$/iu.exec(header)?.[1]?.toLowerCase();
  const explicit = match[2]?.toLowerCase();
  if (explicit && headerUnit && explicit !== headerUnit) fail('conflicting_units', `${label} value and header use different units.`, { column: label });
  const unit = explicit ?? headerUnit ?? units;
  const scale = { mm: 1, mil: 0.0254, in: 25.4 }[unit];
  if (!scale) fail('missing_units', `${label} requires explicit mm, mil or in units.`, { column: label });
  return finite(n * scale, label);
}
function collectRows(rows, read) {
  const records = [], diagnostics = [];
  for (const row of rows) {
    try { records.push(...read(row)); }
    catch (error) { if (!(error instanceof DomainError)) throw error; diagnostics.push({ severity: 'error', code: error.code, row: row.row, ...error.details, message: error.message }); }
  }
  return { records, diagnostics };
}
function duplicateRefs(records, diagnostics) {
  const refs = new Set();
  for (const p of records) { if (refs.has(p.ref)) diagnostics.push({ severity: 'error', code: 'duplicate_reference', row: p.sourceRow, ref: p.ref, message: `Duplicate reference ${p.ref}.` }); refs.add(p.ref); }
}
export function parseBom(content, { mapping = {} } = {}) {
  const [head, ...rows] = parseCsv(content); const map = headerMap(head.cells, mapping); required(map, ['ref', 'partId']);
  const result = collectRows(rows, ({ cells, row }) => {
    if (cells.length !== head.cells.length) fail('column_count', 'BOM row has a different column count from its header.');
    const get = k => map[k] === undefined ? '' : cells[map[k]].trim();
    const refs = get('ref').split(/[\s,;]+/u).filter(Boolean); if (!refs.length) fail('missing_reference', 'BOM reference is required.');
    const base = { partId: identifier(get('partId'), 'partId'), packageId: get('packageId') || null, value: get('value'),
      heightMm: number(get('heightMm'), 'heightMm', { optional: true, length: true, units: 'mm', header: head.cells[map.heightMm] }),
      dnp: bool(get('dnp'), 'dnp'), variants: get('variants').split(/[;|]+/u).map(x => x.trim()).filter(Boolean), sourceRow: row };
    if (base.heightMm !== null && base.heightMm <= 0) fail('invalid_height', 'Part height must be positive.');
    return refs.map(ref => ({ ...base, ref: identifier(ref, 'ref') }));
  });
  duplicateRefs(result.records, result.diagnostics);
  if (result.diagnostics.length) fail('import_invalid', 'BOM import rejected.', { diagnostics: result.diagnostics });
  return { records: result.records, source: sourceReceipt(content, 'bom-csv-v1') };
}

function parseReference(content, options) {
  const [head, ...rows] = parseCsv(content); const map = headerMap(head.cells, options.mapping); required(map, ['ref', 'value', 'packageId', 'x', 'y', 'rotation', 'side']);
  return collectRows(rows, ({ cells, row }) => {
    if (cells.length !== head.cells.length) fail('column_count', 'Placement row has a different column count from its header.');
    const get = k => map[k] === undefined ? '' : cells[map[k]].trim();
    const num = (key, optional = false) => number(get(key), key, { units: options.units, header: head.cells[map[key]], length: key !== 'rotation', optional });
    const type = get('type') || 'placement'; if (!['placement', 'fiducial'].includes(type)) fail('invalid_placement_type', 'Type must be placement or fiducial.');
    return [{ ref: identifier(get('ref'), 'ref'), value: get('value'), packageId: identifier(get('packageId'), 'packageId'), partId: get('partId') || null,
      x: num('x'), y: num('y'), z: 0, rotation: normalizeAngle(num('rotation')), side: importSide(get('side')), heightMm: num('heightMm', true),
      enabled: !bool(get('dnp'), 'dnp'), variants: get('variants').split(/[;|]+/u).map(x => x.trim()).filter(Boolean), type, sourceRow: row,
      metadata: Object.fromEntries(head.cells.flatMap((h, i) => Object.values(map).includes(i) ? [] : [[h, cells[i]]])) }];
  });
}
function tokenize(line) {
  const out = []; let token = '', quoted = false, started = false;
  for (const c of line) {
    if (c === '"') { quoted = !quoted; started = true; }
    else if (/\s/u.test(c) && !quoted) { if (started) { out.push(token); token = ''; started = false; } }
    else { token += c; started = true; }
  }
  if (quoted) fail('malformed_pos', 'Unclosed quote in position record.');
  if (started) out.push(token); return out;
}
function parseKicad(content, options) {
  const lines = textInput(content).split(/\r?\n/u); if (lines.length > LIMITS.rows) fail('input_limit', 'Too many position rows.');
  const declared = [...content.matchAll(/^\s*#+\s*Units?\s*=\s*(mm|inches|inch|mil|in)\s*(?:,\s*Angle\s*=\s*deg\.?)?\s*$/gimu)].map(m => /^inch/iu.test(m[1]) ? 'in' : m[1].toLowerCase());
  if (new Set(declared).size > 1 || (options.units && declared[0] && options.units !== declared[0])) fail('conflicting_units', 'KiCad units conflict with the selected units.');
  const units = declared[0] ?? options.units;
  return collectRows(lines.flatMap((line, i) => !line.trim() || /^\s*#/u.test(line) ? [] : [{ row: i + 1, line }]), ({ line, row }) => {
    const a = tokenize(line); if (a.length !== 7) fail('column_count', 'KiCad .pos requires Ref Val Package PosX PosY Rot Side.');
    const boardSide = importSide(a[6]);
    let x = number(a[3], 'x', { units, length: true }), rotation = number(a[5], 'rotation');
    if (boardSide === 'bottom') {
      if (!['negated-x', 'top-view'].includes(options.kicadBottomConvention)) fail('ambiguous_bottom_convention', 'Bottom KiCad rows require kicadBottomConvention: negated-x (legacy native conversion) or top-view (already canonical XY and angle).');
      if (options.kicadBottomConvention === 'negated-x') { x = -x; rotation = 180 - rotation; }
    }
    return [{ ref: identifier(a[0], 'ref'), value: a[1], packageId: identifier(a[2], 'packageId'), partId: null,
      x, y: number(a[4], 'y', { units, length: true }), z: 0,
      rotation: normalizeAngle(rotation), side: boardSide, heightMm: null, enabled: true, variants: [], type: 'placement', sourceRow: row, metadata: {} }];
  });
}

export function prepareJob(options = {}) {
  if (!options || typeof options !== 'object') fail('invalid_input', 'Import options must be an object.');
  const { format, content, boardId = 'board-1', jobId = 'job-1', variant = null } = options;
  textInput(content);
  if (!['kicad-pos', 'reference-csv'].includes(format)) fail('unsupported_format', 'Supported placement imports are kicad-pos and reference-csv. Native XML must use the qualified native adapter.', { format });
  if (options.coordinateConvention && options.coordinateConvention !== 'openpnp-top-view') fail('unsupported_convention', 'Only explicit OpenPnP top-view XY and placement-angle coordinates are supported. Convert other conventions with a reviewed mapping.');
  const parsed = format === 'kicad-pos' ? parseKicad(content, options) : parseReference(content, options);
  duplicateRefs(parsed.records, parsed.diagnostics);
  if (!parsed.records.length && !parsed.diagnostics.length) parsed.diagnostics.push({ severity: 'error', code: 'empty_import', message: 'No placement rows.' });
  const sources = [sourceReceipt(content, format)]; const byRef = new Map();
  if (options.bomContent !== undefined) { const bom = parseBom(options.bomContent, { mapping: options.bomMapping }); sources.push(bom.source); for (const row of bom.records) byRef.set(row.ref, row); }
  const parts = new Map();
  for (const p of parsed.records) {
    if (p.variants.length && !variant) parsed.diagnostics.push({ severity: 'error', code: 'variant_required', ref: p.ref, message: 'Select a variant for variant-specific placement rows.' });
    p.enabled &&= !p.variants.length || p.variants.includes(variant);
    const bom = byRef.get(p.ref);
    if (options.bomContent !== undefined && !bom && p.type === 'placement') parsed.diagnostics.push({ severity: 'error', code: 'bom_reference_missing', ref: p.ref, row: p.sourceRow, message: 'Placement has no BOM record.' });
    if (bom) {
      if (bom.packageId && bom.packageId !== p.packageId) parsed.diagnostics.push({ severity: 'error', code: 'package_conflict', ref: p.ref, row: p.sourceRow, message: 'BOM and placement package disagree.' });
      if (p.partId && p.partId !== bom.partId) parsed.diagnostics.push({ severity: 'error', code: 'part_conflict', ref: p.ref, row: p.sourceRow, message: 'BOM and placement part identity disagree.' });
      if (bom.value && p.value && bom.value !== p.value) parsed.diagnostics.push({ severity: 'error', code: 'value_conflict', ref: p.ref, row: p.sourceRow, message: 'BOM and placement values disagree.' });
      if (bom.heightMm !== null && p.heightMm !== null && bom.heightMm !== p.heightMm) parsed.diagnostics.push({ severity: 'error', code: 'height_conflict', ref: p.ref, row: p.sourceRow, message: 'BOM and placement heights disagree.' });
      if (bom.variants.length && !variant) parsed.diagnostics.push({ severity: 'error', code: 'variant_required', ref: p.ref, message: 'Select a variant for variant-specific BOM rows.' });
      p.partId = bom.partId; p.heightMm = bom.heightMm ?? p.heightMm; p.enabled &&= !bom.dnp && (!bom.variants.length || bom.variants.includes(variant));
      byRef.delete(p.ref);
    }
    p.partId ??= `part-${digest({ packageId: p.packageId, value: p.value }).slice(0, 20)}`;
    const part = { id: p.partId, packageId: p.packageId, value: p.value, heightMm: p.heightMm };
    const existing = parts.get(p.partId);
    if (existing && digest(existing) !== digest(part)) parsed.diagnostics.push({ severity: 'error', code: 'part_definition_conflict', ref: p.ref, message: 'One part identity has inconsistent package, value or height.' });
    parts.set(p.partId, part);
    if (p.heightMm !== null && p.heightMm <= 0) parsed.diagnostics.push({ severity: 'error', code: 'invalid_height', row: p.sourceRow, ref: p.ref, message: 'Part height must be positive.' });
  }
  for (const ref of byRef.keys()) parsed.diagnostics.push({ severity: 'error', code: 'bom_reference_unplaced', ref, message: 'BOM reference has no placement row; reconcile explicitly.' });
  if (parsed.diagnostics.length) fail('import_invalid', 'Placement import rejected atomically.', { diagnostics: parsed.diagnostics });
  for (const key of ['widthMm', 'heightMm']) if (options[key] !== undefined && options[key] !== null) finite(options[key], key, { min: Number.MIN_VALUE });
  side(options.side ?? 'top'); if (variant !== null) identifier(variant, 'variant');
  const job = { schemaVersion: 1, id: identifier(jobId), units: 'mm', coordinateConvention: 'openpnp-top-view', variant,
    sources, parts: [...parts.values()], boards: [{ id: identifier(boardId), widthMm: options.widthMm ?? null, heightMm: options.heightMm ?? null, placements: parsed.records }],
    panels: [], instances: [{ id: 'load-1', kind: 'board', definitionId: boardId, side: options.side ?? 'top', x: 0, y: 0, z: 0, rotation: 0, enabled: true }], diagnostics: [] };
  return { ...job, revision: digest(job) };
}
