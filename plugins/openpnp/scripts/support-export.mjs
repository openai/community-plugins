// SPDX-License-Identifier: Apache-2.0
// Offline only. No connection, configuration loading, archive extraction or native calls.
import { constants } from 'node:fs';
import { open, lstat, realpath, link, rm } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { inspectJournal } from './diagnostics.mjs';
import toolInputs from '../mcp/tool-inputs.json' with { type: 'json' };

export const SUPPORT_LIMITS = Object.freeze({ operations: 100, artifacts: 16, artifact_bytes: 8 * 1024 * 1024,
  journal_bytes: 512 * 1024 * 1024, records: 250000, projected_bytes: 48 * 1024 * 1024,
  archive_bytes: 64 * 1024 * 1024, metadata_bytes: 65536, depth: 64 });
export class SupportExportError extends Error {
  constructor(code, message) { super(message); this.name = 'SupportExportError'; this.code = code; }
}
const fail = (code, message) => { throw new SupportExportError(code, message); };
const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const hash = x => createHash('sha256').update(x).digest('hex');
const bytes = x => Buffer.from(JSON.stringify(x) + '\n');
const UUID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const SHA = /^[a-f0-9]{64}$/;
const version = /^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$/;
const iso = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/;
const methods = new Set(toolInputs.tools.map(t => t.name));
const operationStates = new Set(['accepted', 'running', 'paused', 'succeeded', 'failed', 'aborted', 'outcome_unknown', 'cancelled']);
const eventTypes = new Set(['operation', 'command_receipt', 'pause_requested', 'abort_requested', 'native_step_intent',
  'native_step_complete', 'cleanup_intent', 'cleanup_complete', 'native_effect_intent', 'native_effect_outcome',
  'native_action_intent', 'native_action_outcome', 'native_action_gap', 'native_placement_checkpoint', 'gui_local_pause_requested']);
const scalarIds = new Set(['operation_id', 'request_id', 'bridge_instance_id', 'machine_id', 'job_id', 'board_load_id', 'loaded_board_id', 'artifact_id']);
const identityKeys = new Set(['placement_key', 'board_instance_id', 'placement_id', 'part_id', 'nozzle_id', 'nozzle_tip_id',
  'feeder_id', 'root_instance_id', 'board_id', 'reference', 'board_load_scope_id']);
const numericKeys = new Set(['schema_version', 'sequence', 'ledger_sequence', 'ownership_epoch', 'native_steps_started',
  'step_index', 'native_step_index', 'action_attempt_observed', 'placement_attempt_observed', 'events_committed',
  'actions_started', 'native_placed_observed', 'independently_verified', 'independently_inspected', 'native_hook_outcomes',
  'requested', 'placed', 'pending', 'excluded', 'feed_count', 'capacity', 'size', 'elapsed_ms', 'through_sequence']);
const booleanKeys = new Set(['native_effect_pending', 'native_call_returned', 'physical_outcome_verified', 'native_cleanup_invoked',
  'repeat_action_performed', 'automatic_replay', 'retry_blocked', 'independently_verified', 'physical_effect_verification',
  'physical_load_verified', 'native_placed_status', 'durability_fault', 'unresolved_action_fault', 'requires_reconciliation',
  'hardware_qualified', 'physical_qualification', 'configuration_saved', 'unknown_outcomes_resolved', 'valid', 'enabled',
  'homed', 'standstill_confirmed', 'cleanup_performed', 'physical_effect_performed', 'initialized', 'native_effect_started',
  'requires_new_control_session', 'pending_abort', 'cooperative']);
const containerKeys = new Set(['result', 'error', 'context', 'scope', 'job', 'native_action_ledger', 'native_action_recovery',
  'pending_actions', 'unresolved_actions', 'safety_gaps', 'last_checkpoint', 'outcomes', 'artifact', 'metadata', 'standstill', 'completion']);
const enums = new Set([...operationStates, 'intent', 'native_hook_returned', 'unknown', 'absent', 'prepared', 'validated',
  'completed', 'ready', 'native-placement-starting-hook', 'native-placement-before-assembly-hook', 'native-placement-complete-hook',
  'feed', 'pick', 'release', 'align', 'discard', 'nozzle_tip_load', 'nozzle_tip_unload', 'native-job-step', 'camera-capture',
  'raw', 'settled', 'top', 'bottom', 'Top', 'Bottom', 'logical-run-scope', 'native-simulator', 'native-job-hook',
  'native-hook-target', 'observed-nozzle-model', 'not-exposed-by-hook', 'before-hook-occurrence',
  'placement-starting-hook-occurrence', 'fence-if-prior-native-hook-outcome-is-unresolved', 'initialize', 'next', 'abort',
  'processor-next', 'processor-abort', 'placement-starting', 'before-assembly',
  'native-abort-returned', 'no-native-job-step-started', 'physical-effect-not-independently-observed',
  'alignment-finally-hook-without-result', 'another-native-before-hook-without-matching-after-hook',
  'another-native-action-with-unresolved-prior-action', 'after-hook-context-mismatch',
  'native-placement-context-not-in-bound-job', 'after-hook-without-matching-intent',
  'journal-ended-before-matching-durable-after-hook', 'abandoned-after-simulator-reset',
  'native-action-outcome-unknown', 'prior-instance-interrupted', 'command-outcome-unknown', 'restart-isolated-simulator',
  'Feeder.BeforeFeed', 'Feeder.AfterFeed', 'Nozzle.BeforePick', 'Nozzle.AfterPick', 'Nozzle.BeforePlace', 'Nozzle.AfterPlace',
  'Vision.PartAlignment.Before', 'Vision.PartAlignment.After', 'Job.BeforeDiscard', 'Job.AfterDiscard',
  'NozzleTip.BeforeLoad', 'NozzleTip.Loaded', 'NozzleTip.BeforeUnload', 'NozzleTip.Unloaded',
  'Job.Placement.Starting', 'Job.Placement.BeforeAssembly', 'Job.Placement.Complete',
  'image/png', 'application/zip', 'application/json', 'native-job-document', 'configuration-backup', 'run-report',
  'capture-returned', 'not-requested', 'not-reported-by-native-api', 'not-established-by-capture-return']);
const enumKeys = new Set(['state', 'kind', 'hook', 'after_hook', 'physical_outcome', 'previous_physical_outcome', 'reason',
  'recovery', 'resolution', 'native_call', 'native_effect_kind', 'attempt_basis', 'placement_context', 'nozzle_tip_context',
  'native_retry_policy', 'internal_retry_index', 'board_load_authority', 'loaded_side', 'side', 'mime_type', 'capture_status',
  'capture_mode', 'settling_outcome', 'image_validity']);
const codes = new Set(['OUTCOME_UNKNOWN', 'RECOVERY_REQUIRED', 'JOURNAL_FAULT', 'JOURNAL_CAPACITY', 'JOURNAL_RECORD_CAPACITY',
  'CONFIGURATION_FAULT', 'STALE_REVISION', 'BUSY', 'LEASE_EXPIRED', 'UNAUTHORIZED', 'ARTIFACT_STORAGE_CAPACITY',
  'NATIVE_ERROR', 'NATIVE_FAILURE', 'NATIVE_ACTION_DURABILITY_UNKNOWN', 'NATIVE_ACTION_OUTCOME_UNKNOWN',
  'NATIVE_ADMISSION_REJECTED', 'SCRIPT_POLICY_REJECTED', 'OWNERSHIP_REVOKED', 'CAMERA_CALIBRATION_ACTIVE', 'PORTABLE_PROFILE_LIMIT']);

// Fields not listed here never enter the archive, even if named token/session/password
// inside a nested result or a key. Native user-controlled identities are hashed.
function project(value, stats, depth = 0) {
  if (depth > SUPPORT_LIMITS.depth || ++stats.nodes > 200000) fail('SUPPORT_RECORD_LIMIT', 'Selected fact record exceeds structural bounds.');
  if (Array.isArray(value)) {
    if (value.length > SUPPORT_LIMITS.records) fail('SUPPORT_RECORD_LIMIT', 'Selected fact array exceeds bounds.');
    return value.map(v => object(v) || Array.isArray(v) ? project(v, stats, depth + 1) : (stats.omitted++, { redacted: true }));
  }
  const result = {};
  for (const [key, v] of Object.entries(value)) {
    let accepted = false;
    if (scalarIds.has(key) && (v === null || typeof v === 'string' && UUID.test(v))) { result[key] = v; accepted = true; }
    else if (identityKeys.has(key) && (v === null || typeof v === 'string' && v.length <= 4096)) {
      result[key + '_sha256'] = v === null ? null : hash(v); accepted = true;
    } else if (['action_id', 'event_id', 'unresolved_action_id'].includes(key) && typeof v === 'string' &&
      /^[a-f0-9-]{36}\/(?:native-action|native-ledger|unmatched-hook)-[1-9][0-9]{0,9}$/.test(v) && UUID.test(v.slice(0, 36))) { result[key] = v; accepted = true; }
    else if (numericKeys.has(key) && Number.isSafeInteger(v) && v >= 0) { result[key] = v; accepted = true; }
    else if (booleanKeys.has(key) && typeof v === 'boolean') { result[key] = v; accepted = true; }
    else if (enumKeys.has(key) && typeof v === 'string' && enums.has(v)) { result[key] = v; accepted = true; }
    else if (key === 'method' && methods.has(v)) { result[key] = v; accepted = true; }
    else if (key === 'code' && codes.has(v)) { result[key] = v; accepted = true; }
    else if (['config_revision', 'board_load_revision'].includes(key) && typeof v === 'string' && /^(?:cfg|load)-[0-9]{1,16}$/.test(v)) { result[key] = v; accepted = true; }
    else if (['sha256', 'job_revision'].includes(key) && typeof v === 'string' && SHA.test(v)) { result[key] = v; accepted = true; }
    else if (['accepted_at', 'updated_at', 'occurred_at', 'observed_at'].includes(key) && typeof v === 'string' && iso.test(v)) { result[key] = v; accepted = true; }
    else if (key === 'outcomes' && object(v)) {
      result[key] = {};
      for (const [kind, count] of Object.entries(v)) {
        if (/^(?:(?:feed|pick|release|align|discard|nozzle_tip_load|nozzle_tip_unload):(?:native_hook_returned|outcome_unknown)|context_gaps)$/.test(kind) && Number.isSafeInteger(count) && count >= 0) result[key][kind] = count;
        else stats.omitted++;
      }
      accepted = true;
    } else if (containerKeys.has(key) && (object(v) || Array.isArray(v))) { result[key] = project(v, stats, depth + 1); accepted = true; }
    if (!accepted) stats.omitted++;
  }
  return result;
}

async function directory(p) {
  const stat = await lstat(p);
  if (!stat.isDirectory() || stat.isSymbolicLink() || process.platform !== 'win32' && (stat.uid !== process.getuid() || (stat.mode & 0o022)))
    fail('SUPPORT_PATH', 'Selected directories must be owned directories without links or shared write access.');
  return stat;
}
async function parents(root, relative) {
  if (path.isAbsolute(relative) || relative.split('/').some(s => !s || s === '.' || s === '..')) fail('SUPPORT_PATH', 'Only fixed relative inputs are supported.');
  let current = root; const rootInfo = await directory(root); const chain = [[root, rootInfo.dev, rootInfo.ino]];
  for (const piece of relative.split('/').slice(0, -1)) {
    current = path.join(current, piece); const stat = await directory(current); chain.push([current, stat.dev, stat.ino]);
  }
  return chain;
}
async function verifyParents(chain) {
  for (const [p, dev, ino] of chain) { const info = await directory(p); if (info.dev !== dev || info.ino !== ino) fail('SUPPORT_SOURCE_CHANGED', 'Input directory changed during export.'); }
}
async function input(root, relative, limit, retain = true) {
  const chain = await parents(root, relative); const file = path.join(root, relative);
  const fd = await open(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const info = await fd.stat();
    if (!info.isFile() || info.size > limit || process.platform !== 'win32' && (info.uid !== process.getuid() || info.nlink !== 1)) fail('SUPPORT_INPUT', 'Selected input must be a bounded owned regular file with one link.');
    const digest = createHash('sha256'); const chunks = []; let size = 0;
    for await (const chunk of fd.createReadStream({ autoClose: false })) {
      size += chunk.length; if (size > limit) fail('SUPPORT_INPUT_LIMIT', 'Input grew beyond its limit.');
      digest.update(chunk); if (retain) chunks.push(chunk);
    }
    const after = await fd.stat(); await verifyParents(chain);
    const current = await lstat(file);
    if (info.dev !== current.dev || info.ino !== current.ino || current.isSymbolicLink() || after.size !== info.size || after.mtimeMs !== info.mtimeMs || after.ctimeMs !== info.ctimeMs || size !== info.size)
      fail('SUPPORT_SOURCE_CHANGED', 'Input changed during export; no complete bundle was published.');
    return { data: retain ? Buffer.concat(chunks) : undefined, sha256: digest.digest('hex'), size };
  } finally { await fd.close(); }
}
function json(data) { try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(data)); } catch { fail('SUPPORT_JSON', 'Selected metadata is not valid UTF-8 JSON.'); } }
function requireShape(value, keys) { if (!object(value) || Object.keys(value).some(k => !keys.includes(k))) fail('SUPPORT_SELECTION', 'Unknown support selection fields.'); }
function tarHeader(name, size) {
  if (!/^[a-z0-9/_.-]{1,99}$/.test(name)) fail('SUPPORT_PATH', 'Invalid generated archive entry.');
  const b = Buffer.alloc(512); b.write(name); b.write('0000600\0', 100); b.write('0000000\0', 108); b.write('0000000\0', 116);
  b.write(size.toString(8).padStart(11, '0') + '\0', 124); b.write('00000000000\0', 136); b.fill(32, 148, 156);
  b[156] = 48; b.write('ustar\0', 257); b.write('00', 263);
  let sum = 0; for (const byte of b) sum += byte; b.write(sum.toString(8).padStart(6, '0') + '\0 ', 148); return b;
}

/** Creates a new standard uncompressed USTAR archive. No output replaces existing data.
 * operationIds select complete available line history; artifactSelections explicitly opt
 * into exact opaque camera PNG or native job-document ZIP bytes by UUID + SHA256. */
export async function exportSupportBundle(options) {
  requireShape(options, ['stateDir', 'output', 'operationIds', 'artifactSelections']);
  const { stateDir, output, operationIds = [], artifactSelections = [] } = options;
  if (typeof stateDir !== 'string' || !path.isAbsolute(stateDir) || typeof output !== 'string' || !path.isAbsolute(output)) fail('SUPPORT_PATH', 'State and new output paths must be absolute.');
  if (!Array.isArray(operationIds) || operationIds.length > SUPPORT_LIMITS.operations || operationIds.some(v => typeof v !== 'string' || !UUID.test(v)) || new Set(operationIds).size !== operationIds.length) fail('SUPPORT_SELECTION', 'Select at most 100 unique lowercase operation UUIDs.');
  if (!Array.isArray(artifactSelections) || artifactSelections.length > SUPPORT_LIMITS.artifacts) fail('SUPPORT_SELECTION', 'Select at most 16 artifacts.');
  const artifactIds = new Set();
  for (const selection of artifactSelections) {
    requireShape(selection, ['artifact_id', 'sha256', 'kind']);
    if (!UUID.test(selection.artifact_id ?? '') || !SHA.test(selection.sha256 ?? '') || !['camera', 'job-document'].includes(selection.kind) || artifactIds.has(selection.artifact_id)) fail('SUPPORT_SELECTION', 'Artifacts require distinct UUID, SHA256 and camera or job-document scope.');
    artifactIds.add(selection.artifact_id);
  }
  await directory(stateDir); const root = await realpath(stateDir); await directory(root);
  const outputParent = await realpath(path.dirname(output)); const parentInfo = await directory(outputParent);
  const publicationParent = [[outputParent, parentInfo.dev, parentInfo.ino]];
  if (outputParent === root || outputParent.startsWith(root + path.sep)) fail('SUPPORT_PATH', 'Support output must be outside operational state.');
  const destination = path.join(outputParent, path.basename(output));
  try { await lstat(destination); fail('SUPPORT_OUTPUT_EXISTS', 'Support output already exists.'); } catch (e) { if (e.code !== 'ENOENT') throw e; }
  const installed = await input(root, 'installation.json', SUPPORT_LIMITS.metadata_bytes); const receipt = json(installed.data);
  if (!object(receipt) || receipt.schema_version !== 1 || typeof receipt.version !== 'string' || receipt.version.length > 48 || !version.test(receipt.version) || !SHA.test(receipt.bridge_sha256 ?? '') || !/^[a-f0-9]{40}$/.test(receipt.upstream_commit ?? '')) fail('SUPPORT_INSTALLATION', 'Installed bridge receipt is invalid.');
  const jarPath = `bridge/${receipt.version}/openpnp-codex-bridge.jar`;
  // The receipt path is checked, never followed as an arbitrary path.
  if (typeof receipt.bridge_jar !== 'string' || path.resolve(receipt.bridge_jar) !== path.join(root, jarPath) && path.resolve(receipt.bridge_jar) !== path.join(path.resolve(stateDir), jarPath)) fail('SUPPORT_INSTALLATION', 'Installed bridge receipt points outside its fixed installation.');
  const jar = await input(root, jarPath, 16 * 1024 * 1024, false);
  const build = await input(root, `bridge/${receipt.version}/build-manifest.json`, 1024 * 1024); const manifest = json(build.data);
  if (!object(manifest) || jar.sha256 !== receipt.bridge_sha256 || manifest.bridge_sha256 !== jar.sha256 || manifest.upstream_commit !== receipt.upstream_commit || manifest.bridge_version !== receipt.version) fail('SUPPORT_INSTALLATION', 'Installed bridge provenance does not match its bytes.');
  const machine = await input(root, 'journal/machine-id', 128); const machineId = machine.data.toString('utf8').trim();
  if (!UUID.test(machineId)) fail('SUPPORT_INSTALLATION', 'Recorded machine identity is invalid.');
  const packageRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
  const packageManifest = await input(packageRoot, '.codex-plugin/plugin.json', SUPPORT_LIMITS.metadata_bytes);
  const packageJson = json(packageManifest.data);
  const packageServer = await input(packageRoot, 'mcp/server.mjs', 16 * 1024 * 1024, false);
  const packageContract = await input(packageRoot, 'mcp/tool-inputs.json', 1024 * 1024, false);
  const packageBuild = await input(packageRoot, 'bridge/build-manifest.json', 1024 * 1024);
  const packageBuildJson = json(packageBuild.data);
  const provenance = { schema_version: 1, machine_id: machineId, machine_identity_source: 'journal/machine-id',
    installed_bridge: { version: receipt.version, upstream_commit: receipt.upstream_commit, sha256: jar.sha256, bytes: jar.size,
      installation_receipt_sha256: installed.sha256, build_manifest_sha256: build.sha256 },
    exporter_package: { name: 'openpnp', version: version.test(packageJson.version ?? '') ? packageJson.version : 'unrecognized',
      manifest_sha256: packageManifest.sha256, mcp_server_sha256: packageServer.sha256, tool_inputs_sha256: packageContract.sha256,
      build_manifest_sha256: packageBuild.sha256, installed_bridge_matches_package: packageBuildJson.bridge_sha256 === jar.sha256 },
    running_bridge_identity: 'not-observed-offline', native_runtime_files_verified: false, physical_state: 'unknown', hardware_qualified: false };
  for (const key of ['bootstrap_sha256', 'patched_native_jar_sha256', 'runtime_manifest_sha256']) if (SHA.test(manifest[key] ?? '')) provenance.installed_bridge[key] = manifest[key];
  const chosen = new Set(operationIds), found = new Set(), selected = [], anomalies = [], allOperations = new Map(), counts = {};
  let projectedBytes = 0, selectedRecords = 0, omittedFields = 0, unrecognizedRecords = 0;
  const charge = data => { projectedBytes += data.length; if (projectedBytes > SUPPORT_LIMITS.projected_bytes) fail('SUPPORT_CAPACITY', 'Selected facts exceed the support bundle limit; no partial operation export was published.'); return data; };
  // Reject linked journal components and hardlinked journal files before shared scanning.
  const journalParents = await parents(root, 'journal/operations.jsonl'); const journalInfo = await lstat(path.join(root, 'journal/operations.jsonl'));
  if (!journalInfo.isFile() || journalInfo.isSymbolicLink() || journalInfo.nlink !== 1) fail('SUPPORT_INPUT', 'Journal must be an unlinked regular file.');
  const diagnostics = await inspectJournal(root, {
    onAnomaly(item) { if (anomalies.length >= SUPPORT_LIMITS.records) fail('SUPPORT_CAPACITY', 'Journal anomalies exceed support bounds.'); anomalies.push(charge(bytes(item))); },
    onRecord(event, line) {
      if (!object(event) || !object(event.payload)) return;
      const p = event.payload, id = p.operation_id;
      if (event.type === 'operation' && UUID.test(id ?? '') && Number.isSafeInteger(event.sequence) && event.sequence > (allOperations.get(id)?.last_sequence ?? 0)) {
        if (allOperations.size >= 20000 && !allOperations.has(id)) fail('SUPPORT_CAPACITY', 'Operation inventory exceeds support bounds.');
        allOperations.set(id, { operation_id: id, state: operationStates.has(p.state) ? p.state : 'unrecognized',
          method: methods.has(p.method) ? p.method : 'unrecognized', last_sequence: event.sequence });
      }
      if (!chosen.has(id)) return;
      found.add(id); if (++selectedRecords > SUPPORT_LIMITS.records) fail('SUPPORT_CAPACITY', 'Selected operation records exceed support bounds.');
      const stats = { omitted: 0, nodes: 0 }; const facts = project(p, stats); omittedFields += stats.omitted;
      const recognized = eventTypes.has(event.type); if (!recognized) unrecognizedRecords++;
      const entry = { ...line, sequence: Number.isSafeInteger(event.sequence) && event.sequence > 0 ? event.sequence : null,
        type: recognized ? event.type : 'unrecognized-event-type',
        ...(UUID.test(event.bridge_instance_id ?? '') ? { bridge_instance_id: event.bridge_instance_id } : {}),
        ...(iso.test(event.occurred_at ?? '') ? { occurred_at: event.occurred_at } : {}), facts,
        redaction: { omitted_fields: stats.omitted, raw_record_included: false, schema_recognized: recognized } };
      counts[entry.type] = (counts[entry.type] ?? 0) + 1; selected.push(charge(bytes(entry)));
    },
  });
  await verifyParents(journalParents); const journalAfter = await lstat(path.join(root, 'journal/operations.jsonl'));
  if (journalAfter.dev !== journalInfo.dev || journalAfter.ino !== journalInfo.ino || journalAfter.isSymbolicLink()) fail('SUPPORT_SOURCE_CHANGED', 'Journal identity changed during export.');
  if (operationIds.some(id => !found.has(id))) fail('SUPPORT_OPERATION_NOT_FOUND', 'A selected operation has no parseable record in the captured journal prefix.');
  const unresolved = [...allOperations.values()].filter(op => !['succeeded', 'failed', 'aborted', 'cancelled'].includes(op.state));
  const selectionSummary = { selected_operation_ids: operationIds, selected_records: selectedRecords, event_counts: counts,
    selected_records_omitted: 0, omitted_fields: omittedFields, unrecognized_records: unrecognizedRecords,
    source_structurally_complete: diagnostics.structurally_complete, source_changed_during_read: diagnostics.source.changed_during_read,
    all_selected_parseable_records_in_prefix_included: true, raw_selected_records_complete: false,
    unresolved_operation_inventory: unresolved, unresolved_operations_omitted: 0,
    policy: 'Every parseable selected operation record is projected in source order. Unknown event shapes retain their line hash and allowlisted facts. Unsafe fields are counted and omitted, never copied. Malformed records cannot be attributed to an operation.',
    unlinked_events: 'Events without a selected operation_id, including native feed and board-load records without that field, are not attributed or exported as selected operation facts.' };
  const entries = [ ['provenance.json', bytes(provenance)], ['diagnostics.json', bytes(diagnostics)],
    ['selection.json', charge(bytes(selectionSummary))], ['operations.jsonl', Buffer.concat(selected)], ['anomalies.jsonl', Buffer.concat(anomalies)] ];
  const artifactReceipts = [];
  for (const selection of artifactSelections) {
    const metadata = await input(root, `journal/${selection.artifact_id}.metadata.json`, SUPPORT_LIMITS.metadata_bytes); const r = json(metadata.data);
    if (!object(r) || r.artifact_id !== selection.artifact_id || r.sha256 !== selection.sha256 || !Number.isSafeInteger(r.size) || r.size < 0 || r.size > SUPPORT_LIMITS.artifact_bytes || !object(r.metadata)) fail('SUPPORT_ARTIFACT', 'Selected artifact receipt does not match its scope or digest.');
    if (selection.kind === 'camera' ? r.mime_type !== 'image/png' || typeof r.metadata.camera_id !== 'string' : r.mime_type !== 'application/zip' || r.metadata.kind !== 'native-job-document') fail('SUPPORT_ARTIFACT_SCOPE', 'Only explicitly selected native camera PNG and job-document ZIP payloads are supported.');
    const artifact = await input(root, `journal/${selection.artifact_id}.artifact`, SUPPORT_LIMITS.artifact_bytes);
    if (artifact.sha256 !== selection.sha256 || artifact.size !== r.size) fail('SUPPORT_ARTIFACT_HASH', 'Selected artifact bytes do not match their receipt and requested digest.');
    if (selection.kind === 'camera' ? !artifact.data.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])) : !artifact.data.subarray(0, 4).equals(Buffer.from([80,75,3,4]))) fail('SUPPORT_ARTIFACT_FORMAT', 'Selected artifact signature does not match its native format.');
    const name = `artifacts/${selection.artifact_id}.${selection.kind === 'camera' ? 'png' : 'zip'}`;
    entries.push([name, artifact.data]); artifactReceipts.push({ ...selection, entry: name, size: artifact.size, metadata_sha256: metadata.sha256,
      data_class: selection.kind === 'camera' ? 'explicit-raw-camera-image' : 'explicit-raw-board-and-job-document', opaque_bytes: true, content_redacted: false });
  }
  const scope = { schema_version: 1, kind: 'openpnp-offline-support-export', archive_format: 'ustar', created_at: new Date().toISOString(),
    artifacts: artifactReceipts, limits: SUPPORT_LIMITS, action_replay_performed: false, network_used: false, native_state_modified: false,
    may_authorize_action: false, physical_state: 'unknown', source_journal_preserved: true,
    excludes: ['credentials', 'connection/auth data', 'session grants', 'free text', 'user scripts', 'configuration XML', 'arbitrary external files', 'unselected artifact payloads', 'MCP response store'],
    privacy: 'Allowlisted journal scalars and hashed native identities only. Exact selected opaque image/document bytes may contain sensitive design data; no payload scrub or extraction is performed.',
    retention: 'This observational copy neither rotates nor deletes the runtime journal or artifacts. Runtime checkpoint/compaction, retention policy, support import and full forensic recovery remain separate work.',
    entries: entries.map(([name, data]) => ({ name, bytes: data.length, sha256: hash(data) })) };
  entries.unshift(['manifest.json', bytes(scope)]);
  const total = entries.reduce((n, [, data]) => n + 512 + Math.ceil(data.length / 512) * 512, 1024);
  if (total > SUPPORT_LIMITS.archive_bytes) fail('SUPPORT_CAPACITY', 'Selected support archive exceeds 64 MiB; nothing was published.');
  await verifyParents(publicationParent);
  const temporary = path.join(outputParent, `.openpnp-support-${randomUUID()}.tmp`); const fd = await open(temporary, 'wx', 0o600);
  const archiveHash = createHash('sha256');
  try {
    for (const [name, data] of entries) for (const block of [tarHeader(name, data.length), data, Buffer.alloc((512 - data.length % 512) % 512)]) { await fd.writeFile(block); archiveHash.update(block); }
    const end = Buffer.alloc(1024); await fd.writeFile(end); archiveHash.update(end); await fd.sync(); await fd.close();
    await verifyParents(publicationParent); await link(temporary, destination);
    try { const dir = await open(outputParent, 'r'); try { await dir.sync(); } finally { await dir.close(); } }
    catch { fail('SUPPORT_PUBLICATION_UNCERTAIN', 'Support archive exists but directory durability could not be confirmed; inspect the existing output before retrying.'); }
    return { schema_version: 1, kind: scope.kind, output: destination, sha256: archiveHash.digest('hex'), size: total,
      selected_operation_count: operationIds.length, selected_records: selectedRecords, selected_artifact_count: artifactSelections.length,
      source_structurally_complete: diagnostics.structurally_complete, source_changed_during_read: diagnostics.source.changed_during_read,
      publication: 'complete', native_state_modified: false, network_used: false, may_authorize_action: false, physical_state: 'unknown' };
  } finally { await fd.close().catch(() => {}); await rm(temporary, { force: true }).catch(() => {}); /* Never remove a published output. */ }
}
