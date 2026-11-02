// SPDX-License-Identifier: Apache-2.0
// Validate the declared diagnostic profile; hardware authority is never inferred.
export const CONTROLLER_PROFILE = 'owned-tagged-controller-diagnostic-v1';
export const CONTROLLER_PROTOCOL = 'owned-tagged-gcode-v1';
export const CONTROLLER_DRIVER = 'org.openpnp.codex.prototype.tagged.OwnedTaggedGcodeDriver';
export const CONTROLLER_TOOL = 'openpnp_run_controller_diagnostic';
export const CONTROLLER_TOOLS = Object.freeze(['get_capabilities', 'get_status', 'get_configuration', 'get_operation', 'get_request_status', 'get_events', 'get_control_session', 'request_control_session', 'renew_control_session', 'release_control_session', 'run_controller_diagnostic'].map(x => `openpnp_${x}`));
const uuid = value => typeof value === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value);
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const fault = message => Object.assign(new Error(message), { code: 'INCOMPATIBLE_BRIDGE' });
export function requireControllerProfile(c, { expectedBridgeSha256, fresh = false } = {}) {
  if (c?.simulator_profile !== CONTROLLER_PROFILE) {
    if (fresh || (Array.isArray(c?.tools) && c.tools.includes(CONTROLLER_TOOL))) throw fault('A controller diagnostic requires its dedicated owned simulator profile.');
    return false;
  }
  if (c.simulation !== true || c.hardware_qualified !== false || c.physical_standstill_verified !== false || c.motion_completion_observed !== false ||
      c.native_driver !== CONTROLLER_DRIVER || c.controller_protocol !== CONTROLLER_PROTOCOL || !hash(c.bridge_artifact_sha256) ||
      (expectedBridgeSha256 && c.bridge_artifact_sha256 !== expectedBridgeSha256) || !uuid(c.bridge_instance_id) ||
      typeof c.machine_id !== 'string' || !c.machine_id || c.machine_id.length > 128 || !uuid(c.controller_diagnostic?.controller_instance_id) ||
      c.controller_diagnostic?.profile !== CONTROLLER_PROFILE || c.controller_diagnostic?.physical_qualification !== false ||
      !Array.isArray(c.configuration_changes) || c.configuration_changes.length !== 0 ||
      !Array.isArray(c.tools) || c.tools.length !== new Set(c.tools).size || c.tools.some(t => !CONTROLLER_TOOLS.includes(t)) || !c.tools.includes('openpnp_get_capabilities') ||
      JSON.stringify(c.fixed_recipe) !== JSON.stringify(['bind','connect:G21,G90','identify:M115','close']) || c.fresh_launch_required !== true)
    throw fault('Owned diagnostic capabilities do not match the fixed profile and qualification limits.');
  if (fresh && (c.controller_diagnostic.generation_spent !== false || c.tools.length !== CONTROLLER_TOOLS.length))
    throw fault('Readiness requires a fresh unspent controller and the complete restricted diagnostic tool set.');
  return true;
}
