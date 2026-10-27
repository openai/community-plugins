import { PluginError } from './errors.mjs';
import { INLINE_JSON_BYTES } from './responses.mjs';

export const PROGRESS_TOOLS = new Set(['openpnp_get_status', 'openpnp_get_operation', 'openpnp_get_request_status']);
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const has = (value, key) => record(value) && Object.hasOwn(value, key);
const fields = value => value.split(' ');
const ids = fields('material_setup_revision controller_instance_id operation_id request_id bridge_instance_id machine_id session_id job_id config_revision job_revision board_load_revision ownership_epoch');
const state = fields('state status outcome code found valid enabled homed active initialized native_busy job_state active_operation_id journal_fault configuration_fault');
const timing = fields('accepted_at updated_at observed_at snapshot_at through_sequence expires_in_ms ttl_seconds lease_state');
const counts = fields('requested placed independently_inspected pending enabled_placements excluded already_placed error_count warning_count');
const effects = fields('native_steps_started native_steps_completed native_effect_pending native_effect_kind native_effect_started native_call_returned effect_outcome_unknown repeat_action_performed automatic_replay physical_outcome physical_effect_verification physical_qualification hardware_qualified simulation_only simulator_origin independently_verified requires_reconciliation');

// This is an explicit polling representation, not a lossy substitute for a full
// response. Only fixed, bounded native facts are copied; missing facts stay absent.
function pick(value, names) {
  if (value === null) return null;
  const out = {};
  for (const name of names) {
    if (!has(value, name)) continue;
    const item = value[name];
    if (item !== null && typeof item !== 'boolean' && !(typeof item === 'number' && Number.isFinite(item)) && typeof item !== 'string')
      throw new PluginError('INVALID_PROGRESS_RESPONSE', `Native progress field ${name} is not a scalar. Use a full read to inspect the response.`);
    if (typeof item === 'string' && Buffer.byteLength(JSON.stringify(item)) > 1024)
      throw new PluginError('INVALID_PROGRESS_RESPONSE', `Native progress field ${name} exceeds its bound. Use a full read to inspect the response.`);
    out[name] = item;
  }
  return out;
}
function nested(out, source, key, project) { if (has(source, key)) out[key] = source[key] === null ? null : project(source[key]); }
function arrayCount(out, source, key) { if (has(source, key) && Array.isArray(source[key])) out[`${key}_count`] = source[key].length; }
function message(out, source) {
  if (!has(source, 'message') || typeof source.message !== 'string') return;
  // Diagnostics may be long. Error codes and known outcomes above remain exact.
  out.message = source.message.slice(0, 384);
  if (out.message !== source.message) out.message_details = { characters: source.message.length, full_view_required: true };
}
function error(value) { const out = pick(value, ['code', 'type', 'state', 'outcome']); message(out, value); return out; }
function knownBody(value) {
  const out = pick(value, ['captured', 'state', 'job_state']);
  nested(out, value, 'result', item => result(item, 1));
  nested(out, value, 'body_failure', error);
  return out;
}
function completion(value) {
  const out = pick(value, [...ids, 'submission_id', 'phase', 'native_wrapper_completed', 'native_wrapper_succeeded',
    'physical_outcome_verified', 'body_entered', 'body_exited', 'completion_publication_attempted', 'ownership_retained']);
  nested(out, value, 'wrapper_error', error);
  // The observation is one fixed nested DTO, not a recursive completion chain.
  nested(out, value, 'completion_observation', item => {
    const observation = pick(item, ['phase', 'native_wrapper_completed', 'native_wrapper_succeeded', 'body_entered', 'body_exited']);
    nested(observation, item, 'wrapper_error', error); return observation;
  });
  return out;
}
function publicationFault(value) {
  const out = pick(value, ['code', 'durable', 'repeat_action_performed']);
  nested(out, value, 'error', error); nested(out, value, 'known_body_outcome', knownBody);
  nested(out, value, 'completion_observation', completion);
  return out;
}
function result(value, depth = 0) {
  const out = pick(value, [...ids, ...state, ...counts, ...effects, 'mode', 'native_processor_preflight_executed', 'side_effects_performed', 'profile', 'completed_recipe', 'steps_attempted', 'outcome_unknown', 'error_type', 'motion_completion_observed', 'physical_standstill_verified', 'nozzle_id', 'nozzle_tip_id', 'sensor_id', 'check_kind', 'native_verdict', 'sample_count', 'reading_units', 'part_state_inferred', 'occupancy_authority']);
  message(out, value); nested(out, value, 'error', error);
  if (depth < 1) nested(out, value, 'job', item => result(item, depth + 1));
  if (depth < 1) nested(out, value, 'known_body_outcome', knownBody);
  nested(out, value, 'wrapper_error', error);
  nested(out, value, 'observation', controllerObservation);
  nested(out, value, 'uncommitted_observation', controllerUncommitted);
  nested(out, value, 'counts', item => pick(item, [...counts, 'pending_placements', 'placed_placements', 'excluded_placements', 'total_placements']));
  for (const key of ['errors', 'warnings', 'placements']) arrayCount(out, value, key);
  return out;
}
function ledger(value) {
  const out = pick(value, fields('events_committed actions_started native_placed_observed independently_verified durability_fault unresolved_action_fault'));
  arrayCount(out, value, 'pending_actions');
  nested(out, value, 'scope', item => pick(item, [...ids, 'board_load_id', 'scope_id']));
  if (record(value?.outcomes)) {
    const keys = Object.keys(value.outcomes); out.outcome_types_count = keys.length;
    out.outcome_counts_complete = keys.length <= 32; out.outcomes = {};
    for (const key of keys.slice(0, 32)) {
      const count = value.outcomes[key];
      if (key.length > 128 || !Number.isSafeInteger(count) || count < 0 || ['__proto__', 'prototype', 'constructor'].includes(key)) {
        out.outcome_counts_complete = false; continue;
      }
      out.outcomes[key] = count;
    }
  }
  return out;
}
function recovery(value) {
  const out = pick(value, [...ids, ...effects, 'events_committed', 'native_hook_outcomes']);
  for (const key of ['unresolved_actions', 'safety_gaps']) arrayCount(out, value, key);
  return out;
}
function vacuumJournal(value) {
  if (!record(value)) throw new PluginError('INVALID_PROGRESS_RESPONSE', 'Native vacuum journal is not an object.');
  if (has(value, 'lifecycle_fault') && typeof value.lifecycle_fault !== 'boolean')
    throw new PluginError('INVALID_PROGRESS_RESPONSE', 'Native vacuum lifecycle fault must be an explicit boolean.');
  const out = pick(value, fields('profile observations observation_limit nozzle_limit lifecycle_fault recovered_history execution_authority_restored physical_occupancy_verified hardware_qualified reconciliation_supported'));
  if (has(value, 'pending')) {
    if (!Array.isArray(value.pending) || value.pending.length > 256) throw new PluginError('INVALID_PROGRESS_RESPONSE', 'Native vacuum pending observations exceed the supported bounded array.');
    out.pending_count = value.pending.length;
  }
  if (has(value, 'nozzles')) {
    if (!Array.isArray(value.nozzles) || value.nozzles.length > 64) throw new PluginError('INVALID_PROGRESS_RESPONSE', 'Native vacuum nozzle observations exceed the supported bounded array.');
    out.nozzles = []; out.nozzles_count = value.nozzles.length;
    // Aggregate faults across every bounded native row so a fault outside the
    // visible prefix cannot be mistaken for an all-clear. Bindings and raw
    // readings remain available in the full view, never reconstructed here.
    out.sticky_fault_count = 0;
    for (const row of value.nozzles) {
      if (!record(row) || typeof row.sticky_fault !== 'boolean')
        throw new PluginError('INVALID_PROGRESS_RESPONSE', 'Native vacuum nozzle lacks an explicit fault observation.');
      if (row.sticky_fault) out.sticky_fault_count++;
    }
    for (const row of value.nozzles.slice(0, 16)) {
      const selected = pick(row, fields('nozzle_id state recorded_state sticky_fault reason observation_count returned_count failed_count last_observation_id last_bridge_instance_id historical'));
      if (Buffer.byteLength(JSON.stringify([...out.nozzles, selected])) > 8192) break;
      out.nozzles.push(selected);
    }
    out.nozzles_complete = out.nozzles.length === value.nozzles.length;
  }
  return out;
}
function controllerObservation(value) {
  const out = pick(value, ['phase', 'observed_at', 'observation_sequence', 'physical_qualification', 'native_authority_restored', 'motion_completion_observed', 'physical_standstill_verified', 'error_type']);
  nested(out, value, 'native', item => {
    const native = pick(item, ['profile', 'native_task', 'owner_thread_id', 'model_binding_sha256', 'connect_attempted', 'owner_fenced', 'closed', 'current_model_validation_performed_by_snapshot', 'durable_receipt', 'physical_qualification']);
    nested(native, item, 'protocol', protocol => pick(protocol, ['profile', 'native_driver', 'generation', 'commands_attempted', 'native_connected_flag', 'native_connect_completed', 'channel_open', 'reader_alive', 'reader_uncaught_error_type', 'transport_phase', 'transport_fault', 'admission_not_fenced', 'wire_bytes_read', 'wire_bytes_written', 'native_response_queue_size', 'native_confirmation_queue_size', 'physical_qualification', 'physical_standstill_verified']));
    return native;
  });
  return out;
}
function controllerUncommitted(value) {
  const out = pick(value, ['publication_confirmed']);
  nested(out, value, 'admission', item => pick(item, [...ids, 'request_digest', 'durable_admission_confirmed', 'native_dispatch_performed', 'physical_qualification']));
  for (const key of ['intent', 'known_native_outcome']) nested(out, value, key, item => {
    const summary = pick(item, [...ids, 'step', 'step_index', 'dispatched', 'native_returned', 'error_type']);
    nested(summary, item, 'before', controllerObservation); nested(summary, item, 'after', controllerObservation);
    return summary;
  });
  return out;
}
function controller(value) {
  const out = pick(value, [...ids, 'profile', 'generation_spent', 'retired', 'native_authority_restored', 'physical_qualification']);
  nested(out, value, 'observation', controllerObservation);
  nested(out, value, 'uncommitted_observation', controllerUncommitted);
  nested(out, value, 'binding', item => pick(item, [...ids, 'profile', 'owner_generation']));
  nested(out, value, 'pending', item => pick(item, [...ids, 'step', 'step_index']));
  arrayCount(out, value, 'steps');
  if (has(value, 'uncommitted_observation')) out.uncommitted_observation_present = value.uncommitted_observation !== null;
  return out;
}
function operation(value) {
  const out = pick(value, [...ids, ...state, ...timing, ...effects, 'method']);
  nested(out, value, 'result', result); nested(out, value, 'error', error);
  nested(out, value, 'native_action_ledger', ledger); nested(out, value, 'native_action_recovery', recovery);
  nested(out, value, 'controller_recovery', controller);
  nested(out, value, 'vacuum_sensing_journal', vacuumJournal);
  nested(out, value, 'native_completion', completion); nested(out, value, 'publication_fault', publicationFault);
  if (has(value, 'result')) out.result_observation = 'Last native transition result. During accepted/running it can describe an earlier pause; use get_status progress.job_progress with its native freshness fields for current counts.';
  return out;
}

function machineProgress(value) {
  const out = pick(value, ['snapshot_at', 'config_revision', 'enabled', 'homed', 'speed', 'position_source']);
  if (has(value, 'feeders')) {
    if (!Array.isArray(value.feeders)) throw new PluginError('INVALID_PROGRESS_RESPONSE', 'Native feeder inventory is not an array.');
    out.feeders = [];
    out.feeders_count = value.feeders.length;
    for (const row of value.feeders.slice(0, 16)) {
      if (!record(row)) throw new PluginError('INVALID_PROGRESS_RESPONSE', 'Native feeder inventory entry is not an object.');
      const selected = pick(row, ['id', 'class', 'part_id', 'enabled', 'capacity', 'feed_count', 'virtual_supply']);
      if (Buffer.byteLength(JSON.stringify([...out.feeders, selected])) > 8192) break;
      out.feeders.push(selected);
    }
    out.feeders_complete = out.feeders.length === value.feeders.length;
  }
  return out;
}

const metricFields = fields('uptime_ms heap_used_bytes heap_committed_bytes heap_max_bytes event_buffer_count operation_count request_count plan_count artifact_count artifact_metadata_cache_count artifact_bytes artifact_memory_content_bytes journal_bytes job_count_recomputations machine_snapshot_refreshes');
function metricsProgress(value) {
  const out = pick(value, metricFields);
  for (const value of Object.values(out)) {
    if (value !== null && (!Number.isSafeInteger(value) || value < 0)) throw new PluginError('INVALID_PROGRESS_RESPONSE', 'Native resource metric is not a nonnegative safe integer.');
  }
  return out;
}

export function projectProgress(tool, value, nativeArgs = {}) {
  if (!PROGRESS_TOOLS.has(tool) || !record(value)) throw new PluginError('INVALID_PROGRESS_RESPONSE', 'Progress view requires a supported native read response.');
  let out;
  if (tool === 'openpnp_get_operation') out = operation(value);
  else if (tool === 'openpnp_get_request_status') {
    out = pick(value, [...ids, 'found']);
    nested(out, value, 'operation', operation);
    nested(out, value, 'uncommitted_admission', item => pick(item, [...ids, 'request_digest', 'durable_admission_confirmed', 'native_dispatch_performed', 'physical_qualification']));
    for (const key of ['command_receipt', 'session_receipt']) nested(out, value, key, item => pick(item, [...ids, ...state, ...timing, ...effects, 'method']));
  } else {
    out = pick(value, [...ids, ...state, ...timing]);
    nested(out, value, 'machine', machineProgress);
    nested(out, value, 'metrics', metricsProgress);
    nested(out, value, 'controller_diagnostic', controller);
    nested(out, value, 'controller_history', controller);
    nested(out, value, 'job_progress', item => pick(item, [...ids, ...state, ...timing, ...counts]));
    nested(out, value, 'native_action_ledger', ledger);
    nested(out, value, 'vacuum_sensing_journal', vacuumJournal);
    nested(out, value, 'native_submission', item => {
      const pending = completion(item); nested(pending, item, 'publication_fault', publicationFault); return pending;
    });
    nested(out, value, 'gui_ownership', item => {
      const summary = pick(item, [...ids, ...state, ...timing, 'mode', 'owner', 'revoked', 'local_grant', 'native_ownership_held', 'local_grant_required', 'simulator_only', 'physical_qualification']);
      nested(summary, item, 'last_error', error); return summary;
    });
    nested(out, value, 'material_loads', item => {
      const summary = pick(item, [...ids, ...timing, 'authority', 'physical_inventory_verified', 'native_authority_restored', 'recovered_from_journal', 'available_trays_observed_at']);
      for (const key of ['loads', 'pending_changes', 'pending_feeds', 'available_trays']) arrayCount(summary, item, key);
      return summary;
    });
    nested(out, value, 'board_loads', item => {
      const summary = pick(item, [...ids, 'authority', 'physical_load_verified', 'restart_presence_confirmation_required', 'pending_changes', 'complete_native_history']);
      arrayCount(summary, item, 'roots'); arrayCount(summary, item, 'loads'); return summary;
    });
  }
  out.response_view = {
    name: 'progress', schema_version: 1, full_details_included: false, retained_snapshot: false,
    full_read: { tool, arguments: { ...nativeArgs, view: 'full' } },
    semantics: 'Selected live polling fields; no full response was archived. A full read is a new observation, not reconstruction of this sample. Native timestamps and sequence numbers are copied unchanged; no lease or freshness is inferred.',
  };
  if (Buffer.byteLength(JSON.stringify(out)) > INLINE_JSON_BYTES)
    throw new PluginError('INVALID_PROGRESS_RESPONSE', 'Selected native progress fields exceed their inline bound. Use a full read.');
  return out;
}
