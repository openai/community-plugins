// Verification clients can reconstruct an explicitly retained reply without repeating
// its native action. Codex normally reads only the detail pages needed for its task.
import { createHash } from 'node:crypto';

const MAX_BYTES = 24 * 1024 * 1024;
const PAGE_BYTES = 16384;
const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const digest = value => createHash('sha256').update(value).digest('hex');

/** readPage accepts page arguments and returns the tool's structuredContent. */
export async function readCompleteResponse(summary, readPage) {
  if (!isRecord(summary) || summary.truncated !== true) return summary;
  const fail = (code, message) => {
    // Keep a known native outcome available even when detail retrieval fails.
    throw Object.assign(new Error(message), { code, known_response: summary, native_action_repeated: false });
  };
  const ensure = (ok, message) => { if (!ok) fail('RESPONSE_DETAILS_INTEGRITY', message); };
  const retained = summary.response_retention;
  if (!isRecord(retained) || retained.details_available !== true)
    fail('RESPONSE_DETAILS_UNAVAILABLE', 'Full response details are unavailable. Preserve the known outcome; do not repeat the native action.');
  ensure(/^[a-f0-9]{64}$/.test(retained.response_id) && retained.response_id === retained.sha256 &&
    Number.isSafeInteger(retained.bytes) && retained.bytes > 0 && retained.bytes <= MAX_BYTES &&
    retained.bytes === summary.original_json_bytes && retained.page_tool === 'openpnp_read_response_page' && retained.root_pointer === '',
  'The retained response descriptor is inconsistent or exceeds the 24 MiB limit.');
  const output = Buffer.alloc(retained.bytes);
  let offset = 0;
  while (offset < output.length) {
    let page;
    try { page = await readPage({ response_id: retained.response_id, pointer: '', format: 'bytes', offset, limit: PAGE_BYTES }); }
    catch { fail('RESPONSE_DETAILS_UNAVAILABLE', 'A read-only response page could not be retrieved. Preserve the known native outcome; no action was repeated.'); }
    ensure(isRecord(page) && page.response_id === retained.response_id && page.pointer === '' && page.format === 'bytes' &&
      page.encoding === 'base64' && page.offset === offset && page.limit === PAGE_BYTES && page.total === output.length &&
      page.selected_sha256 === retained.sha256 && typeof page.data === 'string' && page.data.length <= Math.ceil(PAGE_BYTES / 3) * 4,
    'Response page identity, length, or byte encoding does not match the retained reply.');
    const chunk = Buffer.from(page.data, 'base64');
    ensure(chunk.toString('base64') === page.data && chunk.length === Math.min(PAGE_BYTES, output.length - offset) &&
      page.count === chunk.length && page.next_offset === offset + chunk.length && page.next_offset <= output.length &&
      page.eof === (page.next_offset === output.length),
    'Response page has invalid bytes, makes no progress, or crosses its declared boundary.');
    chunk.copy(output, offset); offset = page.next_offset;
  }
  ensure(digest(output) === retained.sha256, 'Reconstructed response digest does not match the retained reply.');
  let value;
  try { value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(output)); }
  catch { fail('RESPONSE_DETAILS_INTEGRITY', 'Retained response is not valid UTF-8 JSON.'); }
  ensure(isRecord(value), 'Retained response must be a JSON object.');
  const envelopeMetadata = new Set(['truncated', 'response_retention', 'original_json_bytes', 'inline_limit_bytes']);
  let compared = 0;
  function compareKnownFacts(known, complete, depth) {
    ensure(++compared <= 4096 && depth <= 16, 'Known response summary exceeds its comparison bounds.');
    if (isRecord(known) && known.truncated === true && (typeof known.json_pointer === 'string' || known.root_bytes_required === true)) return;
    if (known === null || typeof known !== 'object') {
      ensure(Object.is(known, complete), 'Retained response differs from a known summary fact.'); return;
    }
    ensure(isRecord(complete), 'Retained response differs from a known summary envelope.');
    for (const [key, item] of Object.entries(known)) {
      if ((depth === 0 && envelopeMetadata.has(key)) || key === 'omitted_properties') continue;
      ensure(Object.hasOwn(complete, key), 'Retained response is missing a known summary property.');
      compareKnownFacts(item, complete[key], depth + 1);
    }
  }
  compareKnownFacts(summary, value, 0);
  return value;
}
