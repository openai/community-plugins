import { readFile, lstat } from 'node:fs/promises';
import path from 'node:path';
import { PluginError } from './errors.mjs';

const MAX_RESPONSE_BYTES = 24 * 1024 * 1024;
const operationMethods = new Set(['run_controller_diagnostic', 'set_machine_enabled', 'home_machine', 'execute_motion', 'capture_camera', 'measure_sensor', 'verify_part_state', 'apply_configuration', 'prepare_job', 'validate_job', 'save_job', 'load_job', 'register_board_load', 'register_material_load', 'inspect_job', 'plan_placement_edits', 'apply_placement_edits', 'plan_placement_structure', 'apply_placement_structure', 'start_job', 'step_job', 'pause_job', 'resume_job', 'abort_job', 'test_feeder', 'control_actuator', 'change_nozzle_tip', 'backup_configuration', 'export_portable_configuration', 'export_run_report', 'run_calibration', 'validate_calibration', 'locate_fiducials', 'list_issues', 'restore_configuration', 'reconcile_operation'].map(name => `openpnp_${name}`));
const operationStates = new Set(['accepted', 'running', 'paused', 'succeeded', 'failed', 'aborted', 'outcome_unknown', 'unknown', 'cancelled']);
operationMethods.add('openpnp_request_board_inspection');
operationMethods.add('openpnp_request_sensing_reconciliation');
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
function scrub(value, token) {
  if (typeof value === 'string') return value.replaceAll(token, '[REDACTED]');
  if (Array.isArray(value)) return value.map(item => scrub(item, token));
  if (record(value)) return Object.fromEntries(Object.entries(value).map(([key, item]) => [key.replaceAll(token, '[REDACTED]'), scrub(item, token)]));
  return value;
}
function validateResult(method, result, params) {
  if (!record(result)) throw new Error('Native result must be an object');
  if (operationMethods.has(method) || method === 'openpnp_get_operation') {
    if (typeof result.operation_id !== 'string' || !result.operation_id || !operationStates.has(result.state)) throw new Error('Invalid native operation receipt');
  }
  if (['openpnp_measure_sensor', 'openpnp_verify_part_state', 'openpnp_request_sensing_reconciliation'].includes(method)) {
    if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(result.operation_id) ||
        result.request_id !== params.request_id || result.config_revision !== params.expected_config_revision) {
      throw new Error('Sensing operation receipt does not bind the admitted request and configuration');
    }
  }
  if (method === 'openpnp_request_control_session' && (typeof result.session_id !== 'string' || !result.session_id || !Number.isSafeInteger(result.ownership_epoch))) throw new Error('Invalid native ownership receipt');
  if (['openpnp_plan_motion', 'openpnp_plan_configuration'].includes(method) && (typeof result.plan_id !== 'string' || !record(result.plan))) throw new Error('Invalid native plan');
  return result;
}

export function validateEndpoint(value) {
  let url;
  try { url = new URL(value); } catch { throw new PluginError('INVALID_CONNECTION', 'The bridge URL is invalid.'); }
  if (url.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(url.hostname) ||
      !url.port || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new PluginError('INVALID_CONNECTION', 'Use an HTTP loopback IP and explicit port, without credentials, path, query, or fragment.');
  }
  return url;
}

async function readPrivateFile(file, maxBytes) {
  if (typeof file !== 'string' || !path.isAbsolute(file)) throw new PluginError('INVALID_CONNECTION', 'Connection and token files must use absolute paths.');
  const stats = await lstat(file);
  if (!stats.isFile() || stats.isSymbolicLink() || stats.size > maxBytes) throw new PluginError('INVALID_CONNECTION', 'Connection or token file is not a bounded regular file.');
  if (process.platform !== 'win32' && ((stats.mode & 0o077) !== 0 || stats.uid !== process.getuid())) {
    throw new PluginError('INSECURE_CREDENTIAL_FILE', 'Connection and token files must belong to the current user with permissions 0600.');
  }
  return readFile(file, 'utf8');
}

export async function loadConnection(file = process.env.OPENPNP_CONNECTION_FILE) {
  if (!file) throw new PluginError('NOT_CONNECTED', 'No OpenPnP connection is configured. Use the bundled CLI to configure a local bridge.');
  try {
    const config = JSON.parse(await readPrivateFile(file, 16384));
    if (!config || Object.keys(config).some(key => !['url', 'tokenFile', 'machineId', 'schemaVersion'].includes(key))) {
      throw new PluginError('INVALID_CONNECTION', 'The connection file contains unsupported fields.');
    }
    const url = validateEndpoint(config.url);
    const token = (await readPrivateFile(config.tokenFile, 4096)).trim();
    if (!/^[A-Za-z0-9_-]{32,256}$/.test(token)) throw new PluginError('INVALID_CONNECTION', 'The bridge token must be a 32–256 character URL-safe random secret.');
    return { url, token, machineId: config.machineId };
  } catch (error) {
    if (error instanceof PluginError) throw error;
    throw new PluginError('INVALID_CONNECTION', 'The connection or token file cannot be read or parsed.');
  }
}

export class BridgeClient {
  constructor({ connectionFile, timeoutMs = 15000, fetchImpl = fetch } = {}) {
    this.connectionFile = connectionFile;
    this.timeoutMs = timeoutMs;
    this.fetchImpl = fetchImpl;
  }

  async call(method, params = {}, { mutating = false } = {}) {
    const connection = await loadConnection(this.connectionFile);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    let sent = false;
    try {
      const body = JSON.stringify({ method, params });
      if (Buffer.byteLength(body) > 8 * 1024 * 1024) throw new PluginError('REQUEST_TOO_LARGE', 'The bridge request exceeds 8 MiB.');
      sent = true;
      const response = await this.fetchImpl(new URL('/rpc', connection.url), {
        method: 'POST', headers: { 'Authorization': `Bearer ${connection.token}`, 'Content-Type': 'application/json' },
        body, signal: controller.signal, redirect: 'error',
      });
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) throw new PluginError('BRIDGE_AUTH_REJECTED', 'The local bridge rejected authentication.');
        throw new Error('Unexpected bridge HTTP status');
      }
      const reader = response.body.getReader();
      const chunks = []; let size = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > MAX_RESPONSE_BYTES) { await reader.cancel(); throw new Error('Oversize response'); }
        chunks.push(value);
      }
      const envelope = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (!record(envelope) || Object.keys(envelope).some(key => !['result', 'error'].includes(key)) ||
          (Object.hasOwn(envelope, 'result') === Object.hasOwn(envelope, 'error'))) throw new Error('Invalid bridge envelope');
      if (Object.hasOwn(envelope, 'error')) {
        if (!record(envelope.error) || typeof envelope.error.code !== 'string' || !envelope.error.code || typeof envelope.error.message !== 'string') throw new Error('Invalid bridge error');
        throw new PluginError(scrub(envelope.error.code, connection.token), scrub(envelope.error.message, connection.token), scrub(envelope.error.details ?? {}, connection.token));
      }
      const result = validateResult(method, envelope.result, params);
      if (connection.machineId && ['openpnp_get_capabilities', 'openpnp_get_status'].includes(method) && result.machine_id !== connection.machineId) {
        throw new PluginError('MACHINE_ID_MISMATCH', 'The native bridge machine identity differs from the configured connection. Reconnect to the intended simulator.');
      }
      return scrub(result, connection.token);
    } catch (error) {
      if (error instanceof PluginError) throw error;
      if (mutating && sent) throw new PluginError('OUTCOME_UNKNOWN', 'The bridge response was lost. The action may have been admitted. Reconcile the request before any retry.', { request_id: params.request_id ?? null, method });
      throw new PluginError('BRIDGE_UNAVAILABLE', 'The local OpenPnP bridge could not be reached or returned an invalid response.');
    } finally { clearTimeout(timeout); }
  }
}
