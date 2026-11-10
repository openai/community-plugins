/** Bounded, dependency-free CSV exchange for explicit PCBA data contracts. */
export class DomainError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = 'DomainError';
    this.code = code;
    this.details = details;
  }
}

const fail = (code, message, details) => { throw new DomainError(code, message, details); };
const MAX_BYTES = 4 * 1024 * 1024;
const MAX_ROWS = 100_000;
const MAX_CELL = 8192;
const MAX_COLUMNS = 256;

/** RFC 4180 quoting; accepts CRLF/LF/CR, preserves quoted newlines and source rows. */
export function parseCsv(text) {
  if (typeof text !== 'string') fail('INVALID_CSV', 'CSV input must be UTF-8 text.');
  if (Buffer.byteLength(text, 'utf8') > MAX_BYTES) fail('INPUT_TOO_LARGE', 'CSV exceeds the 4 MiB limit.');
  if (text.includes('\0')) fail('INVALID_CSV', 'CSV cannot contain NUL bytes.');
  const input = text.replace(/^\uFEFF/, '');
  const records = [];
  let cells = [], cell = '', quoted = false, closed = false, line = 1, rowStart = 1, touched = false;
  const addCell = () => {
    if (cell.length > MAX_CELL) fail('CELL_TOO_LARGE', 'CSV cell exceeds 8192 characters.', { row: rowStart });
    cells.push(cell);
    if (cells.length > MAX_COLUMNS) fail('TOO_MANY_COLUMNS', 'CSV exceeds 256 columns.', { row: rowStart });
    cell = ''; closed = false;
  };
  const addRow = () => {
    addCell();
    // Truly blank physical lines are harmless; a quoted empty cell is not discarded.
    if (touched || cells.length > 1 || cells[0] !== '') records.push({ row: rowStart, cells });
    if (records.length > MAX_ROWS) fail('TOO_MANY_ROWS', 'CSV exceeds 100000 records.');
    cells = []; touched = false;
  };
  for (let i = 0; i < input.length; i++) {
    const ch = input[i];
    if (quoted) {
      if (ch === '"') {
        if (input[i + 1] === '"') { cell += '"'; i++; }
        else { quoted = false; closed = true; }
      } else if (ch === '\r' || ch === '\n') {
        cell += ch;
        if (ch === '\r' && input[i + 1] === '\n') { cell += '\n'; i++; }
        line++;
      } else cell += ch;
      if (cell.length > MAX_CELL) fail('CELL_TOO_LARGE', 'CSV cell exceeds 8192 characters.', { row: rowStart });
      continue;
    }
    if (closed && ch !== ',' && ch !== '\r' && ch !== '\n') {
      fail('INVALID_CSV_QUOTING', 'Unexpected text after a closing quote.', { row: line });
    }
    if (ch === '"') {
      if (cell.length) fail('INVALID_CSV_QUOTING', 'A quote must start a cell or be escaped inside a quoted cell.', { row: line });
      quoted = true; touched = true;
    } else if (ch === ',') { addCell(); touched = true; }
    else if (ch === '\r' || ch === '\n') {
      addRow();
      if (ch === '\r' && input[i + 1] === '\n') i++;
      line++; rowStart = line;
    } else { cell += ch; touched = true; }
    if (cell.length > MAX_CELL) fail('CELL_TOO_LARGE', 'CSV cell exceeds 8192 characters.', { row: rowStart });
  }
  if (quoted) fail('UNCLOSED_CSV_QUOTE', 'CSV ends inside a quoted field.', { row: rowStart });
  if (touched || cells.length || cell.length || closed) addRow();
  if (!records.length) fail('EMPTY_CSV', 'CSV must contain a header row.');
  const headers = records[0].cells.map(v => v.trim());
  if (headers.some(h => !h)) fail('INVALID_CSV_HEADER', 'Every CSV column requires a nonempty header.');
  const keys = headers.map(h => h.toLowerCase());
  if (new Set(keys).size !== keys.length) fail('DUPLICATE_CSV_HEADER', 'CSV headers must be unique ignoring case.');
  for (const record of records.slice(1)) {
    if (record.cells.length !== headers.length) fail('CSV_COLUMN_COUNT', 'CSV row has a different number of columns than its header.', { row: record.row, expected: headers.length, observed: record.cells.length });
  }
  return { headers, records: records.slice(1) };
}

const aliases = {
  ref: ['Ref', 'Refs', 'Reference', 'References', 'RefDes', 'Designator', 'Designators'],
  partId: ['PartId', 'Part ID', 'Part'], value: ['Value', 'Val'],
  package: ['Package', 'Footprint'], category: ['Category', 'Assembly Category'],
  populated: ['Populated', 'Populate'], dnp: ['DNP', 'Do Not Populate'], mpn: ['MPN', 'Manufacturer Part Number'],
  x: ['X', 'X(mm)', 'X (mm)', 'X (in)', 'X(in)', 'X (mil)', 'X(mil)', 'PosX', 'Xmm'],
  y: ['Y', 'Y(mm)', 'Y (mm)', 'Y (in)', 'Y(in)', 'Y (mil)', 'Y(mil)', 'PosY', 'Ymm'],
  rotation: ['Rotation', 'Rotation(deg)', 'Rotation (deg)', 'Rot', 'Angle'], side: ['Side', 'Layer'],
};
function columns(headers, mapping, required) {
  if (!mapping || typeof mapping !== 'object' || Array.isArray(mapping)) fail('INVALID_MAPPING', 'Column mapping must be an object.');
  const resolved = {};
  for (const key of Object.keys(mapping)) {
    if (!Object.hasOwn(aliases, key)) fail('INVALID_MAPPING', `Unknown mapped field: ${key}.`);
    if (typeof mapping[key] !== 'string') fail('INVALID_MAPPING', 'Mapped column names must be strings.', { field: key });
  }
  for (const [key, names] of Object.entries(aliases)) {
    const indexes = headers.map((h, i) => ({ h, i })).filter(({ h }) => mapping[key] !== undefined
      ? h === mapping[key] : names.some(a => a.toLowerCase() === h.toLowerCase()));
    if (indexes.length > 1) fail('AMBIGUOUS_MAPPING', `Several columns could mean ${key}; provide an exact mapping.`, { field: key, columns: indexes.map(x => x.h) });
    if (mapping[key] !== undefined && !indexes.length) fail('MISSING_MAPPED_COLUMN', `Mapped column is absent: ${mapping[key]}.`, { field: key });
    resolved[key] = indexes[0]?.i;
    if (required.includes(key) && resolved[key] === undefined) fail('MISSING_COLUMN', `CSV requires a ${key} column or mapping.`, { field: key });
  }
  return resolved;
}
function boolean(text, field, row) {
  const s = text.trim().toLowerCase();
  if (['true', '1', 'yes', 'y'].includes(s)) return true;
  if (['false', '0', 'no', 'n'].includes(s)) return false;
  fail('INVALID_BOOLEAN', `${field} must explicitly be true/false, yes/no, or 1/0.`, { row, field, value: text });
}
const get = (cells, cols, key, fallback = '') => cols[key] === undefined ? fallback : cells[cols[key]].trim();

/** Required: reference(s), PartId, Package, Category and Populated or DNP. */
export function parseBomCsv(text, mapping = {}) {
  const { headers, records } = parseCsv(text);
  const cols = columns(headers, mapping, ['ref', 'partId', 'package', 'category']);
  if (cols.populated === undefined && cols.dnp === undefined) fail('MISSING_POPULATION', 'BOM must explicitly declare Populated or DNP.');
  const result = [];
  for (const { row, cells } of records) {
    const refs = get(cells, cols, 'ref').split(/[\s,;]+/).filter(Boolean);
    if (!refs.length) fail('MISSING_REFERENCE', 'BOM row requires at least one reference.', { row });
    const p = cols.populated === undefined ? undefined : boolean(get(cells, cols, 'populated'), 'Populated', row);
    const d = cols.dnp === undefined ? undefined : boolean(get(cells, cols, 'dnp'), 'DNP', row);
    if (p !== undefined && d !== undefined && p === d) fail('CONFLICTING_POPULATION', 'Populated and DNP disagree.', { row });
    const category = get(cells, cols, 'category').toLowerCase();
    if (!['smt', 'tht', 'mechanical', 'fiducial'].includes(category)) fail('INVALID_CATEGORY', 'Category must be smt, tht, mechanical or fiducial.', { row, value: category });
    for (const ref of refs) {
      result.push({ ref, partId: get(cells, cols, 'partId'), value: get(cells, cols, 'value'), package: get(cells, cols, 'package'), category, populated: p ?? !d, mpn: get(cells, cols, 'mpn') || null, sourceRow: row });
      if (result.length > MAX_ROWS) fail('TOO_MANY_REFERENCES', 'Expanded BOM exceeds 100000 references.');
    }
  }
  return result;
}

function numeric(s, row, field) {
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(s)) fail('INVALID_NUMBER', `${field} must be an unambiguous decimal number.`, { row, field, value: s });
  const n = Number(s);
  if (!Number.isFinite(n) || Math.abs(n) > 1e9) fail('INVALID_NUMBER', `${field} is outside the supported finite numeric range.`, { row, field });
  return n;
}
const angle = n => n % 360 < 0 ? n % 360 + 360 : (n % 360 || 0);
const headerUnits = h => /\((mm|mil|in)\)$/i.exec(h)?.[1]?.toLowerCase() ?? (/^[XY]mm$/i.test(h) ? 'mm' : undefined);

/** Input frame/origin must already be selected by caller; only declared units and bottom convention transform. */
export function parsePlacementCsv(text, { units, mapping = {}, bottomConvention } = {}) {
  const { headers, records } = parseCsv(text);
  const cols = columns(headers, mapping, ['ref', 'x', 'y', 'rotation', 'side']);
  const hx = headerUnits(headers[cols.x]), hy = headerUnits(headers[cols.y]);
  if (hx !== hy && (hx || hy)) fail('CONFLICTING_UNITS', 'X and Y headers must declare the same units.');
  if (units !== undefined && !['mm', 'in', 'mil'].includes(units)) fail('INVALID_UNITS', 'Units must be mm, in or mil.');
  if (units && hx && units !== hx) fail('CONFLICTING_UNITS', 'Explicit units conflict with CSV headers.');
  const selected = units ?? hx;
  if (!selected) fail('AMBIGUOUS_UNITS', 'Declare units explicitly or use matching unit-bearing X/Y headers.');
  if (bottomConvention !== undefined && !['top-view', 'negated-x'].includes(bottomConvention)) fail('INVALID_BOTTOM_CONVENTION', 'Bottom convention must be top-view or negated-x.');
  const scale = { mm: 1, in: 25.4, mil: 0.0254 }[selected];
  return records.map(({ row, cells }) => {
    const sideRaw = get(cells, cols, 'side').toLowerCase();
    const side = ['top', 'front', 'f.cu'].includes(sideRaw) ? 'top' : ['bottom', 'back', 'b.cu'].includes(sideRaw) ? 'bottom' : null;
    if (!side) fail('INVALID_SIDE', 'Side must explicitly identify top/front/F.Cu or bottom/back/B.Cu.', { row, value: sideRaw });
    if (side === 'bottom' && !bottomConvention) fail('AMBIGUOUS_BOTTOM_CONVENTION', 'Bottom placements require an explicit bottomConvention.');
    let xMm = numeric(get(cells, cols, 'x'), row, 'x') * scale;
    const yMm = numeric(get(cells, cols, 'y'), row, 'y') * scale;
    let rotationDeg = numeric(get(cells, cols, 'rotation'), row, 'rotation');
    if (side === 'bottom' && bottomConvention === 'negated-x') { xMm = -xMm; rotationDeg = 180 - rotationDeg; }
    const result = { ref: get(cells, cols, 'ref'), xMm, yMm, rotationDeg: angle(rotationDeg), side, sourceRow: row };
    if (!result.ref) fail('MISSING_REFERENCE', 'Placement row requires a reference.', { row });
    if (cols.package !== undefined) result.package = get(cells, cols, 'package');
    return result;
  });
}

function cell(value) {
  const str = value == null ? '' : String(value);
  return /[",\r\n]/.test(str) ? `"${str.replaceAll('"', '""')}"` : str;
}
export const emitCsv = (headers, rows) => [headers, ...rows].map(row => row.map(cell).join(',')).join('\r\n') + '\r\n';
export function emitBomCsv(pkg) {
  return emitCsv(['Ref', 'PartId', 'Value', 'Package', 'Category', 'Populated', 'MPN'], pkg.bom.map(r => [r.ref, r.partId, r.value, r.package, r.category, r.populated, r.mpn]));
}
export function emitPlacementsCsv(pkg) {
  return emitCsv(['Ref', 'X(mm)', 'Y(mm)', 'Rotation(deg)', 'Side', 'Package'], pkg.placements.map(r => [r.ref, r.xMm, r.yMm, r.rotationDeg, r.side, r.package ?? '']));
}
