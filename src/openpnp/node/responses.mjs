import { createHash, randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { mkdir, lstat, readdir, open, rename, rm } from 'node:fs/promises';
import path from 'node:path';
import { defaultStateDir } from './artifacts.mjs';
import { PluginError } from './errors.mjs';

export const INLINE_JSON_BYTES = 32 * 1024;
export const MAX_RESPONSE_BYTES = 24 * 1024 * 1024;
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const fail = (code, message) => { throw new PluginError(code, message); };
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const pointerToken = value => String(value).replaceAll('~', '~0').replaceAll('/', '~1');
const forbidden = new Set(['__proto__', 'prototype', 'constructor']);
function privateFile(stat, directory = false) {
  if (stat.isSymbolicLink() || (directory ? !stat.isDirectory() : !stat.isFile() || stat.nlink !== 1) ||
      (typeof process.getuid === 'function' && (stat.uid !== process.getuid() || (stat.mode & 0o077) !== 0)))
    fail('INSECURE_RESPONSE_STORE', 'Response storage must be owned by this user, private, and free of links.');
}

/** Exact serialized reply bytes, immutable by content hash; never evicts earlier evidence. */
export class ResponseStore {
  constructor(root = defaultStateDir(), { maxBytes = 256 * 1024 * 1024, maxFiles = 128 } = {}) {
    this.root = root; this.directory = path.join(root, 'responses'); this.maxBytes = maxBytes; this.maxFiles = maxFiles;
  }
  async initialize() {
    await mkdir(this.root, { recursive: true, mode: 0o700 }); privateFile(await lstat(this.root), true);
    await mkdir(this.directory, { mode: 0o700 }).catch(error => { if (error.code !== 'EEXIST') throw error; });
    privateFile(await lstat(this.directory), true);
  }
  async readBytes(responseId) {
    if (typeof responseId !== 'string' || !/^[a-f0-9]{64}$/.test(responseId)) fail('INVALID_RESPONSE_ID', 'Use the retained SHA-256 response identifier.');
    await this.initialize(); let handle;
    try {
      handle = await open(path.join(this.directory, `${responseId}.json`), constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
      const stat = await handle.stat(); privateFile(stat);
      if (stat.size > MAX_RESPONSE_BYTES) fail('RESPONSE_INTEGRITY_FAILURE', 'Stored response exceeds its size bound.');
      const bytes = Buffer.alloc(stat.size + 1); let count = 0;
      while (count < bytes.length) { const read = await handle.read(bytes, count, bytes.length - count, count); if (!read.bytesRead) break; count += read.bytesRead; }
      const data = bytes.subarray(0, count);
      if (count !== stat.size || sha(data) !== responseId) fail('RESPONSE_INTEGRITY_FAILURE', 'Stored response bytes do not match their identifier.');
      return data;
    } catch (error) {
      if (error.code === 'ENOENT') fail('RESPONSE_NOT_FOUND', 'The retained response is unavailable in this installation.');
      if (error.code === 'ELOOP') fail('INSECURE_RESPONSE_STORE', 'Response links are not accepted.');
      throw error;
    } finally { await handle?.close(); }
  }
  async putBytes(bytes) {
    if (!Buffer.isBuffer(bytes) || bytes.length > MAX_RESPONSE_BYTES) fail('RESPONSE_TOO_LARGE', 'Response retention is bounded to 24 MiB of serialized JSON.');
    const response_id = sha(bytes); await this.initialize();
    const lock = path.join(this.directory, '.writer-lock');
    try { await mkdir(lock, { mode: 0o700 }); } catch (error) { if (error.code === 'EEXIST') fail('RESPONSE_STORE_BUSY', 'Another response writer or an interrupted write owns the store; earlier evidence is retained.'); throw error; }
    const temporary = path.join(lock, `${randomUUID()}.json`); let handle;
    try {
      const names = await readdir(this.directory); let used = 0, count = 0;
      for (const name of names) {
        if (name === '.writer-lock') continue;
        if (!/^[a-f0-9]{64}\.json$/.test(name)) fail('RESPONSE_STORE_INVALID', 'Unexpected response-store entry; no evidence was removed.');
        const stat = await lstat(path.join(this.directory, name)); privateFile(stat); used += stat.size; count++;
      }
      if (names.includes(`${response_id}.json`)) {
        await this.readBytes(response_id);
        return { response_id, sha256: response_id, bytes: bytes.length, media_type: 'application/json', deduplicated: true };
      }
      if (count >= this.maxFiles || used + bytes.length > this.maxBytes) fail('RESPONSE_STORE_CAPACITY', 'Response retention is full; earlier evidence was preserved without eviction.');
      handle = await open(temporary, 'wx', 0o600); await handle.writeFile(bytes); await handle.sync(); await handle.close(); handle = undefined;
      await rename(temporary, path.join(this.directory, `${response_id}.json`));
      if (process.platform !== 'win32') { const directory = await open(this.directory, 'r'); try { await directory.sync(); } finally { await directory.close(); } }
      return { response_id, sha256: response_id, bytes: bytes.length, media_type: 'application/json', deduplicated: false };
    } finally { await handle?.close(); await rm(lock, { recursive: true, force: true }); }
  }
  async readPage({ response_id, pointer = '', offset = 0, limit = 100, format = 'json' }) {
    if (typeof pointer !== 'string' || Buffer.byteLength(JSON.stringify(pointer)) > 4096 || !Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > (format === 'json' ? 500 : 16384) || !['json', 'bytes', 'decoded-base64'].includes(format)) fail('INVALID_ARGUMENT', 'Use a bounded pointer, nonnegative offset, and page limit.');
    const bytes = await this.readBytes(response_id); const root = JSON.parse(bytes.toString('utf8')); let value = root;
    if (pointer !== '') {
      if (!pointer.startsWith('/') || pointer.split('/').length > 65 || /~(?![01])/u.test(pointer)) fail('INVALID_JSON_POINTER', 'Use an RFC 6901 JSON pointer with at most 64 segments.');
      for (const encoded of pointer.slice(1).split('/')) {
        const key = encoded.replaceAll('~1', '/').replaceAll('~0', '~');
        if (forbidden.has(key) || value === null || typeof value !== 'object' || !own(value, key) || (Array.isArray(value) && !/^(0|[1-9][0-9]*)$/.test(key))) fail('INVALID_JSON_POINTER', 'Pointer must name an own JSON property or array index; prototype paths are forbidden.');
        value = value[key];
      }
    }
    const base = { response_id, pointer, format, offset, limit };
    if (format !== 'json') {
      let data;
      if (format === 'bytes') data = pointer === '' ? bytes : Buffer.from(JSON.stringify(value));
      else {
        if (typeof value !== 'string') fail('INVALID_BASE64_VALUE', 'The selected value is not canonical base64.');
        data = Buffer.from(value, 'base64'); if (data.toString('base64') !== value) fail('INVALID_BASE64_VALUE', 'The selected value is not canonical base64.');
      }
      if (offset > data.length) fail('INVALID_PAGE_OFFSET', 'Offset exceeds the selected byte length.');
      const chunk = data.subarray(offset, offset + limit);
      return { ...base, total: data.length, count: chunk.length, next_offset: offset + chunk.length, eof: offset + chunk.length === data.length, encoding: 'base64', data: chunk.toString('base64'), selected_sha256: sha(data), byte_semantics: format === 'bytes' ? 'exact retained JSON bytes at root; serialized selected JSON at other pointers' : 'decoded original binary bytes' };
    }
    const kind = Array.isArray(value) ? 'array' : value !== null && typeof value === 'object' ? 'object' : 'scalar';
    const keys = kind === 'object' ? Object.keys(value) : null; const total = kind === 'array' ? value.length : kind === 'object' ? keys.length : 1;
    if (offset > total) fail('INVALID_PAGE_OFFSET', 'Offset exceeds the selected value count.');
    const result = { ...base, kind, total, count: 0, next_offset: offset, eof: offset === total, items: [] };
    for (let index = offset; index < Math.min(offset + limit, total); index++) {
      const key = kind === 'object' ? keys[index] : index;
      const item = kind === 'object' ? { key, value: value[key] } : kind === 'array' ? value[index] : value;
      result.items.push(item);
      if (Buffer.byteLength(JSON.stringify(result)) > INLINE_JSON_BYTES - 1024) {
        result.items.pop();
        if (!result.items.length) {
          const itemPointer = kind === 'scalar' ? pointer : `${pointer}/${pointerToken(key)}`;
          result.oversized_item = { ...(Buffer.byteLength(JSON.stringify(itemPointer)) <= 4096 ? { pointer: itemPointer } : { parent_pointer: pointer, read_parent_bytes: true }), message: 'This individual value exceeds the inline page budget. Read its child pointers or format bytes; this offset was not consumed.' };
        }
        break;
      }
      result.count++; result.next_offset++; result.eof = result.next_offset === total;
    }
    return result;
  }
}

/** Keep existing outcome paths while replacing large detail collections with explicit pointers. */
export function summarizeResponse(value) {
  let nodes = 0; const critical = /(?:^|_)(?:id|state|status|error|code|count|counts|placed|requested|completed|found|outcome|revision|sequence|qualified|valid|enabled|homed|pending|bytes|sha256)$/;
  const important = new Set(['operation_id','request_id','bridge_instance_id','session_id','job_id','state','status','outcome','found','error','result','operation','job','job_progress','config_revision']);
  const envelopes = new Set(['operation','result','job','job_progress','error']);
  const priority = (key, item) => important.has(key) ? (item === null || typeof item !== 'object' ? 3 : 2) : critical.test(key) ? 1 : 0;
  const size = item => Buffer.byteLength(JSON.stringify(item));
  const marker = (pointer, metadata = {}) => ({ truncated: true, ...(pointer.length <= 4096 ? { json_pointer: pointer } : { root_bytes_required: true }), ...metadata });
  function visit(item, pointer, depth, budget, envelope = false) {
    if (item === null || typeof item !== 'object') {
      if (typeof item === 'string' && (size(item) > 512 || size(item) > budget)) return marker(pointer, { string_characters: item.length });
      return item;
    }
    if ((++nodes > 400 && !envelope) || depth > 7) return marker(pointer);
    if (Array.isArray(item)) return marker(pointer, { total_items: item.length });
    const out = Object.create(null), keys = Object.keys(item).sort((a, b) => priority(b, item[b]) - priority(a, item[a])); let included = 0;
    for (const key of keys.slice(0, 40)) {
      const available = budget - size(out) - 512;
      if (available < 256 || key.length > 128) continue;
      out[key] = visit(item[key], `${pointer}/${pointerToken(key)}`, depth + 1, Math.min(available, 8192), envelopes.has(key) && depth < 4);
      if (size(out) > budget - 256) delete out[key]; else included++;
    }
    if (included < keys.length) out.omitted_properties = marker(pointer, { count: keys.length - included });
    return out;
  }
  return visit(value, '', 0, INLINE_JSON_BYTES - 8192);
}

export async function boundedReply(value, store, { forceRetention = false } = {}) {
  const bytes = Buffer.from(JSON.stringify(value));
  if (!forceRetention && bytes.length <= INLINE_JSON_BYTES) return value;
  const summary = summarizeResponse(value);
  let retention;
  try { retention = { ...await store.putBytes(bytes), details_available: true, page_tool: 'openpnp_read_response_page', root_pointer: '', exact_reconstruction: { format: 'bytes', offset: 0, limit: 16384 } }; }
  catch (error) { retention = { details_available: false, details_unavailable: true, storage_error: { code: error.code ?? 'RESPONSE_STORAGE_FAILED', message: 'Full response retention failed. The known native outcome above remains authoritative; do not repeat the action to recover details.' } }; }
  return { ...summary, truncated: true, response_retention: retention, original_json_bytes: bytes.length, inline_limit_bytes: INLINE_JSON_BYTES };
}
