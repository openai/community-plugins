import { requireControllerProfile, CONTROLLER_PROFILE, CONTROLLER_TOOL } from '../../../plugins/openpnp/scripts/controller-profile.mjs';
import Ajv from 'ajv';
import { TOOL_DEFINITIONS, TOOL_BY_NAME } from './contracts.mjs';
import { BridgeClient } from './client.mjs';
import { ArtifactStore } from './artifacts.mjs';
import { PluginError } from './errors.mjs';
import { ResponseStore } from './responses.mjs';
import { PROGRESS_TOOLS, projectProgress } from './progress.mjs';

const ajv = new Ajv({ allErrors: true, strict: true, coerceTypes: false, useDefaults: false });
const validators = new Map(TOOL_DEFINITIONS.map(tool => [tool.name, ajv.compile(tool.inputSchema)]));
export const PINNED_UPSTREAM = '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c';
function requireCompatibility(capabilities) {
  requireControllerProfile(capabilities);
  if (capabilities?.schema_version !== 1 || capabilities?.upstream_commit !== PINNED_UPSTREAM || capabilities?.bridge_version !== '0.1.0') {
    throw new PluginError('INCOMPATIBLE_BRIDGE', 'The native bridge protocol, version, or pinned OpenPnP build does not match this plugin.');
  }
}

export class OpenPnpRuntime {
  constructor({ client = new BridgeClient(), artifacts = new ArtifactStore(), responses = new ResponseStore(), domain } = {}) {
    this.client = client; this.artifacts = artifacts; this.responses = responses; this.domain = domain;
  }

  async call(name, args = {}) {
    const tool = TOOL_BY_NAME.get(name);
    if (!tool) throw new PluginError('UNKNOWN_TOOL', 'This tool is not part of the installed OpenPnP contract.');
    const validate = validators.get(name);
    if (!validate(args)) throw new PluginError('INVALID_ARGUMENT', 'Arguments do not match the tool schema.', {
      issues: validate.errors.map(({ instancePath, keyword, message }) => ({ path: instancePath, rule: keyword, message })),
    });
    const panelBoardChange = name === 'openpnp_plan_placement_structure' &&
      ['clone_board_child', 'remove_board_child'].includes(args.changes[0].action) ? args.changes[0] : null;
    // JSON Schema maxLength counts Unicode code points; native limits count UTF-16 units.
    if (panelBoardChange && (panelBoardChange.parent_instance_id.length > 512 ||
        (panelBoardChange.source_child_id ?? panelBoardChange.child_id).length > 128)) {
      throw new PluginError('INVALID_ARGUMENT', 'Panel selectors exceed native UTF-16 identity limits.');
    }
    if (name === 'openpnp_get_capabilities') {
      const localTools = TOOL_DEFINITIONS.filter(item => item.offline).map(item => item.name);
      try {
        const bridge = await this.client.call(name, args);
        requireCompatibility(bridge);
        const nativeTools = Array.isArray(bridge.tools) ? bridge.tools.map(item => item === 'openpnp_get_artifact' ? 'openpnp_get_native_artifact' : item).filter(item => TOOL_BY_NAME.has(item)) : [];
        return { plugin_version: '0.1.0', schema_version: 1, connected: true, tools: [...new Set([name, ...localTools, ...nativeTools])], bridge };
      } catch (error) {
        if (!['NOT_CONNECTED', 'BRIDGE_UNAVAILABLE', 'BRIDGE_AUTH_REJECTED', 'INVALID_CONNECTION', 'INSECURE_CREDENTIAL_FILE', 'INCOMPATIBLE_BRIDGE', 'MACHINE_ID_MISMATCH'].includes(error.code)) throw error;
        return { plugin_version: '0.1.0', schema_version: 1, connected: false, tools: [name, ...localTools], connection_error: { code: error.code, message: error.message }, machine_state: 'unknown' };
      }
    }
    if (tool.offline) return this.callOffline(name, args);
    const capabilities = await this.client.call('openpnp_get_capabilities', {});
    requireCompatibility(capabilities);
    const nativeName = name === 'openpnp_get_native_artifact' ? 'openpnp_get_artifact' : name;
    if (!Array.isArray(capabilities.tools) || !capabilities.tools.includes(nativeName)) {
      throw new PluginError('UNSUPPORTED_CAPABILITY', 'The connected native bridge does not advertise this operation.', { tool: name });
    }
    if (panelBoardChange && capabilities.panel_board_membership?.profile !== 'panel-board-membership-v1') {
      throw new PluginError('UNSUPPORTED_CAPABILITY', 'The connected bridge does not advertise panel-board-membership-v1.');
    }
    if (name === 'openpnp_request_board_inspection' && (capabilities.loaded_board_inspection?.profile !== 'native-loaded-board-inspection-v1' || capabilities.loaded_board_inspection.request_available !== true)) {
      throw new PluginError('UNSUPPORTED_CAPABILITY', 'The connected bridge does not advertise a local loaded-board inspection form.');
    }
    if (['openpnp_measure_sensor', 'openpnp_verify_part_state'].includes(name)) {
      const sensing = capabilities.vacuum_sensing;
      if (sensing?.profile !== 'native-vacuum-sensing-v1' || sensing.available !== true ||
          sensing.source_profile !== 'controlled-native-vacuum-v1' || sensing.simulation_only !== true || sensing.hardware_qualified !== false) {
        throw new PluginError('UNSUPPORTED_CAPABILITY', 'The connected bridge does not advertise the qualified controlled simulator sensing profile.');
      }
    }
    if (name === 'openpnp_request_sensing_reconciliation') {
      const recovery = capabilities.sensing_reconciliation;
      if (recovery?.profile !== 'native-simulator-sensing-reconciliation-v1' || recovery.available !== true ||
          recovery.simulation_only !== true || recovery.hardware_qualified !== false) {
        throw new PluginError('UNSUPPORTED_CAPABILITY', 'The connected bridge does not advertise its local simulator sensing recovery form.');
      }
      if (args.recovery_kind === 'restart-faulted-job-replacement' && recovery.restart_request_available !== true) {
        throw new PluginError('UNSUPPORTED_CAPABILITY', 'The connected bridge does not advertise an explicit local GUI restart observation form.');
      }
    }
    if (name === 'openpnp_plan_configuration') {
      const available = new Set(capabilities.configuration_changes ?? []);
      const unsupported = [...new Set(args.changes.map(change => change.type).filter(type => !available.has(type)))];
      if (unsupported.length) throw new PluginError('UNSUPPORTED_CHANGE', 'The connected native bridge does not advertise these typed setting adapters.', { changes: unsupported });
    }
    if (name === CONTROLLER_TOOL && (capabilities.simulator_profile !== CONTROLLER_PROFILE || capabilities.controller_diagnostic.controller_instance_id !== args.controller_instance_id)) {
      throw new PluginError('CONTROLLER_INSTANCE_MISMATCH', 'Bind the fresh observed diagnostic controller identity.');
    }
    let nativeArgs = args;
    if (PROGRESS_TOOLS.has(name)) {
      const { view, ...remaining } = args;
      nativeArgs = remaining;
    }
    if (name === 'openpnp_prepare_job' && args.artifact_id) {
      if (args.part_bindings !== undefined && capabilities.canonical_part_bindings?.profile !== 'existing-parts-v1') {
        throw new PluginError('UNSUPPORTED_CAPABILITY', 'The connected bridge does not advertise existing-parts-v1 import bindings.');
      }
      const { artifact_id, ...remaining } = args;
      nativeArgs = { ...remaining, canonical_job: await this.artifacts.get(artifact_id),
        ...(args.part_bindings === undefined ? {} : { canonical_artifact_id: artifact_id }) };

    }
    const result = await this.client.call(nativeName, nativeArgs, { mutating: tool.mutating });
    return PROGRESS_TOOLS.has(name) && args.view === 'progress' ? projectProgress(name, result, nativeArgs) : result;
  }

  async callOffline(name, args) {
    if (name === 'openpnp_read_response_page') return this.responses.readPage(args);
    if (name === 'openpnp_get_artifact') return { artifact_id: args.artifact_id, content: await this.artifacts.get(args.artifact_id) };
    if (!this.domain) throw new PluginError('DOMAIN_UNAVAILABLE', 'The bundled job domain module is unavailable. Reinstall the plugin.');
    if (name === 'openpnp_import_job') {
      const job = this.domain.prepareJob(args);
      const artifact = await this.artifacts.put(job);
      const validation = this.domain.validateJob(job, { mode: 'offline' });
      return { ...artifact, job_id: job.id, revision: job.revision, diagnostics: job.diagnostics, validation };
    }
    if (name === 'openpnp_validate_imported_job') {
      const job = await this.artifacts.get(args.artifact_id);
      return { artifact_id: args.artifact_id, validation: this.domain.validateJob(job, { mode: args.mode ?? 'offline' }) };
    }
    if (name === 'openpnp_record_inspection') {
      const job = await this.artifacts.get(args.job_artifact_id);
      const record = this.domain.recordInspection({
        jobRevision: job.revision, configurationRevision: args.configuration_revision, boardLoadId: args.board_load_id, placementKey: args.placement_key,
        method: args.method, actor: args.actor, observedAt: args.observed_at, presence: args.presence, polarity: args.polarity,
        measurements: args.measurements, tolerances: args.tolerances, evidence: args.evidence,
      });
      this.domain.assessInspectionCoverage({ job, configurationRevision: args.configuration_revision, boardLoadId: args.board_load_id, inspections: [record] });
      return { ...await this.artifacts.put({ kind: 'inspection', record, provenance_validation: 'supplied-references-only', production_qualified: false }), inspection: record, provenance_validation: 'supplied-references-only', production_qualified: false };
    }
    if (name === 'openpnp_assess_inspection_coverage') {
      const job = await this.artifacts.get(args.job_artifact_id);
      const inspections = [];
      for (const id of args.inspection_artifact_ids) {
        const artifact = await this.artifacts.get(id);
        if (artifact.kind !== 'inspection') throw new PluginError('INVALID_ARTIFACT_KIND', 'Inspection coverage requires inspection artifacts.');
        inspections.push(artifact.record);
      }
      return { ...this.domain.assessInspectionCoverage({ job, configurationRevision: args.configuration_revision, boardLoadId: args.board_load_id, inspections }), provenance_validation: 'supplied-references-only' };
    }
    throw new PluginError('UNKNOWN_TOOL', 'The offline tool has no implementation.');
  }
}
