import { createHash } from 'node:crypto';

export class DomainError extends Error {
  constructor(code, message, details = {}) {
    super(message); this.name = 'DomainError'; this.code = code; this.details = details;
  }
  toJSON() { return { code: this.code, message: this.message, details: this.details }; }
}
export const LIMITS = Object.freeze({ bytes: 4 * 1024 * 1024, rows: 100000, field: 8192, depth: 16, placements: 100000 });
export function fail(code, message, details) { throw new DomainError(code, message, details); }
export function finite(value, label, { min = -1e9, max = 1e9 } = {}) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max)
    fail('invalid_number', `${label} must be a finite number in [${min}, ${max}].`, { field: label });
  return value;
}
export function identifier(value, label = 'id') {
  if (typeof value !== 'string' || !value.trim() || value.length > 512 || /[\u0000-\u001f\u007f]/u.test(value))
    fail('invalid_identifier', `${label} must be nonempty text without control characters.`, { field: label });
  return value;
}
export function side(value) {
  if (value !== 'top' && value !== 'bottom') fail('invalid_side', 'Side must be top or bottom.');
  return value;
}
export function normalizeAngle(value) {
  finite(value, 'rotation'); const n = ((value + 180) % 360 + 360) % 360 - 180; return Object.is(n, -0) ? 0 : n;
}
export function stableStringify(value) {
  const ancestors = new Set();
  function walk(v, depth = 0) {
    if (depth > 64) fail('input_limit', 'Object nesting exceeds the limit.');
    if (v === null || typeof v === 'string' || typeof v === 'boolean') return v;
    if (typeof v === 'number') { if (!Number.isFinite(v)) fail('invalid_number', 'Cannot hash non-finite numbers.'); return v; }
    if (typeof v !== 'object' || ancestors.has(v)) fail('invalid_object', 'Expected acyclic JSON data.');
    if (!Array.isArray(v) && ![Object.prototype, null].includes(Object.getPrototypeOf(v))) fail('invalid_object', 'Expected plain JSON objects.');
    ancestors.add(v);
    const out = Array.isArray(v) ? v.map(x => walk(x, depth + 1)) : Object.fromEntries(Object.keys(v).sort().map(k => [k, walk(v[k], depth + 1)]));
    ancestors.delete(v); return out;
  }
  return JSON.stringify(walk(value));
}
export function digest(value) { return createHash('sha256').update(typeof value === 'string' ? value : stableStringify(value)).digest('hex'); }
export function textInput(content) {
  if (typeof content !== 'string') fail('invalid_input', 'Content must be a UTF-8 string.');
  if (Buffer.byteLength(content, 'utf8') > LIMITS.bytes) fail('input_limit', 'Content exceeds 4 MiB.');
  if (/[\u0000\uFFFD]/u.test(content) || !content.isWellFormed()) fail('invalid_encoding', 'NUL, invalid Unicode or replacement characters are not accepted.');
  return content.replace(/^\uFEFF/u, '');
}
export function sourceReceipt(content, format) {
  textInput(content); return { format, encoding: 'utf-8', bytes: Buffer.byteLength(content, 'utf8'), sha256: digest(content) };
}
