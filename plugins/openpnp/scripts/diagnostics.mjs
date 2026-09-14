// SPDX-License-Identifier: Apache-2.0
// Offline, observational journal analysis. This module never connects to a machine.
import { constants } from 'node:fs';
import { open, realpath } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import toolInputs from '../mcp/tool-inputs.json' with { type: 'json' };

const MAX_JOURNAL = 512 * 1024 * 1024;
const MAX_LINE = 16 * 1024 * 1024;
const MAX_OPERATIONS = 20000;
const UUID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
const STATES = new Set(['accepted', 'running', 'paused', 'succeeded', 'failed', 'aborted', 'outcome_unknown', 'cancelled']);
const TYPES = new Set(['operation', 'command_receipt', 'session_receipt', 'session_granted', 'session_released', 'session_expired',
  'pause_requested', 'abort_requested', 'feed_intent', 'feed_complete', 'native_step_intent', 'native_step_complete',
  'cleanup_intent', 'cleanup_complete', 'configuration_change_started', 'configuration_fault']);
const METHODS = new Set(toolInputs.tools.map(tool => tool.name));
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);

async function openOwnedFile(root, relative, limit) {
  const file = path.join(root, relative);
  const parent = await realpath(path.dirname(file));
  if (parent !== root && !parent.startsWith(root + path.sep)) throw new Error('Diagnostic input escapes the selected state directory.');
  const handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const info = await handle.stat();
    if (!info.isFile() || info.size > limit || (process.platform !== 'win32' && info.uid !== process.getuid()))
      throw new Error('Diagnostic input must be a bounded regular file owned by the current user.');
    return { handle, info };
  } catch (error) { await handle.close(); throw error; }
}

// The optional observer is used by the selective support exporter. It receives every
// complete parsed line, including lines the diagnostic state reducer cannot apply.
// Observer failures abort the scan; selected evidence is never silently dropped.
export async function inspectJournal(stateDirectory, { onRecord, onAnomaly } = {}) {
  const root = await realpath(stateDirectory);
  const { handle, info } = await openOwnedFile(root, 'journal/operations.jsonl', MAX_JOURNAL);
  const hash = createHash('sha256');
  const eventCounts = Object.create(null);
  const operations = new Map();
  const anomalies = [];
  let anomalyCount = 0, sequence = 0, lines = 0, records = 0, bytes = 0, tail = Buffer.alloc(0);
  function anomaly(kind, line) { anomalyCount++; if (anomalies.length < 100) anomalies.push({ kind, line }); onAnomaly?.({ kind, line }); }
  function consume(line) {
    lines++;
    if (!line.length) { anomaly('blank-record', lines); return; }
    let event;
    try { event = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(line)); }
    catch { anomaly('malformed-json-or-encoding', lines); return; }
    onRecord?.(event, { line: lines, source_line_sha256: createHash('sha256').update(line).digest('hex') });
    if (!record(event) || !Number.isSafeInteger(event.sequence) || event.sequence <= 0 || !record(event.payload)) {
      anomaly('invalid-event-envelope', lines); return;
    }
    records++;
    if (event.sequence <= sequence) { anomaly('non-increasing-sequence-not-applied', lines); return; }
    if (!sequence && event.sequence !== 1) anomaly('missing-prefix', lines);
    else if (sequence && event.sequence !== sequence + 1) anomaly('sequence-gap', lines);
    sequence = Math.max(sequence, event.sequence);
    const type = TYPES.has(event.type) ? event.type : 'unrecognized-event-type';
    eventCounts[type] = (eventCounts[type] ?? 0) + 1;
    if (type !== 'operation') return;
    const p = event.payload;
    if (!UUID.test(p.operation_id ?? '') || !STATES.has(p.state) || !METHODS.has(p.method)) {
      anomaly('invalid-operation-envelope', lines); return;
    }
    if (!operations.has(p.operation_id) && operations.size >= MAX_OPERATIONS) throw new Error('Diagnostic operation limit exceeded; preserve the original journal.');
    const item = { operation_id: p.operation_id, method: p.method, state: p.state, last_sequence: event.sequence };
    if (Number.isSafeInteger(p.native_steps_started) && p.native_steps_started >= 0) item.native_steps_started = p.native_steps_started;
    operations.set(p.operation_id, item);
  }
  try {
    if (info.size) {
      const stream = handle.createReadStream({ start: 0, end: info.size - 1, autoClose: false });
      for await (const chunk of stream) {
        bytes += chunk.length; hash.update(chunk);
        tail = tail.length ? Buffer.concat([tail, chunk]) : chunk;
        let newline;
        while ((newline = tail.indexOf(10)) !== -1) {
          if (newline > MAX_LINE) throw new Error('Diagnostic journal record exceeds 16 MiB.');
          consume(tail.subarray(0, newline)); tail = tail.subarray(newline + 1);
        }
        if (tail.length > MAX_LINE) throw new Error('Diagnostic journal record exceeds 16 MiB.');
      }
    }
    if (tail.length) anomaly('unterminated-tail-not-applied', lines + 1);
    const after = await handle.stat();
    const stateCounts = Object.create(null);
    for (const op of operations.values()) stateCounts[op.state] = (stateCounts[op.state] ?? 0) + 1;
    const latest = [...operations.values()].sort((a, b) => b.last_sequence - a.last_sequence);
    return {
      schema_version: 1, kind: 'offline-journal-diagnostics', captured_at: new Date().toISOString(),
      source: { relative_path: 'journal/operations.jsonl', bytes_read: bytes, initial_size: info.size, sha256: hash.digest('hex'),
        grew_during_read: after.size > info.size, changed_during_read: after.size !== info.size || after.mtimeMs !== info.mtimeMs,
        read_scope: 'prefix present when the file was opened; concurrent appends are excluded' },
      structurally_complete: anomalyCount === 0 && bytes === info.size,
      records, through_sequence: sequence, event_counts: eventCounts, operation_count: operations.size, operation_state_counts: stateCounts,
      recent_operations: latest.slice(0, 100), recent_operations_omitted: Math.max(0, latest.length - 100),
      unresolved_operation_count: latest.filter(op => ['accepted', 'running', 'paused', 'outcome_unknown'].includes(op.state)).length,
      anomaly_count: anomalyCount, anomalies, anomalies_omitted: Math.max(0, anomalyCount - anomalies.length),
      physical_state: 'unknown', may_authorize_action: false, action_replay_performed: false,
      data_boundary: 'Only allowlisted counters, method/state names and operation UUIDs are exported. Credentials, session grants, free text, configuration and job contents are omitted.',
      interpretation: 'Recorded software states explain prior execution. They do not prove present machine state, individual native substep outcomes, inspection or safe replay.',
    };
  } finally { await handle.close(); }
}
