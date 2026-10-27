// Official MCP SDK -> packaged stdio server -> real pinned OpenPnP simulator.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { performance } from 'node:perf_hooks';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { NATIVE_CHANGE_SCHEMAS } from '../../src/openpnp/node/settings-contracts.mjs';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const connectionFile = process.env.OPENPNP_SENSING_CONNECTION_FILE;
const output = process.env.OPENPNP_SENSING_EVIDENCE_DIR;
const server = path.join(root, 'plugins/openpnp/mcp/server.mjs');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const settingKeys = NATIVE_CHANGE_SCHEMAS.find(s => s.properties.type.const === 'set_vacuum_sensing_settings').required;
const changeFrom = dto => Object.fromEntries(settingKeys.map(k => [k, dto[k]]));

test('official SDK configures sensing, observes native checks and a placed job, and retains a known failed part-off fault',
  { skip: !connectionFile, timeout: 270000 }, async () => {
    assert.ok(output, 'Evidence output is required');
    await mkdir(output, { recursive: true });
    const state = path.join(output, 'mcp-state'); await mkdir(state, { mode: 0o700 });
    const evidence = { kind: 'official-sdk-native-vacuum-sensing', started_at: new Date().toISOString(), node: process.version,
      simulation_only: true, physical_qualification: false, restart_authority_qualified: false, checks: [], operations: [], requests: [] };
    const inputs = [server, path.join(root, 'plugins/openpnp/bridge/openpnp-codex-bridge.jar'), fileURLToPath(import.meta.url)];
    const hashes = async () => Object.fromEntries(await Promise.all(inputs.map(async f => [path.relative(root, f), sha(await readFile(f))])));
    evidence.source_hashes = await hashes();
    const client = new Client({ name: 'native-vacuum-sensing', version: '1' });
    const transport = new StdioClientTransport({ command: process.execPath, args: [server, '--stdio'], cwd: state,
      env: { PATH: process.env.PATH ?? '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: connectionFile }, stderr: 'pipe' });
    let session;
    const check = (name, body) => { body(); evidence.checks.push(name); };
    async function call(name, args = {}) {
      const raw = await client.callTool({ name: `openpnp_${name}`, arguments: args });
      const value = await readCompleteResponse(raw.structuredContent, page => call('read_response_page', page));
      if (raw.isError) throw Object.assign(new Error(value.error.message), value.error);
      return value;
    }
    const status = () => call('get_status', { view: 'progress' });
    async function request(name, args = {}) {
      const bound = { session_id: session, request_id: randomUUID(), expected_config_revision: (await status()).config_revision, ...args };
      evidence.requests.push({ name, ...bound, session_id: '<redacted>' });
      return { bound, handle: await call(name, bound) };
    }
    async function observe(handle) {
      const deadline = performance.now() + 150000; let op = handle;
      while (['accepted', 'running'].includes(op.state)) {
        assert.ok(performance.now() < deadline, 'Original operation observation timed out; no replay');
        await delay(25); op = await call('get_operation', { operation_id: handle.operation_id, view: 'progress' });
      }
      const full = await call('get_operation', { operation_id: handle.operation_id, view: 'full' });
      while ((await status()).native_busy) {
        assert.ok(performance.now() < deadline, 'Native wrapper did not drain; no replay'); await delay(10);
      }
      evidence.operations.push(full); return full;
    }
    async function operation(name, args = {}, expected = 'succeeded') {
      const { handle } = await request(name, args); const op = await observe(handle);
      assert.equal(op.state, expected, JSON.stringify(op)); return op;
    }
    async function rejected(name, args, code) {
      // Native admission may reject before accepting an operation. A queued
      // refusal must also finish without any effect; both outcomes are retained.
      let submitted;
      try { submitted = await request(name, args); }
      catch (error) { assert.equal(error.code, code); evidence.checks.push(`${name}: ${code} before acceptance`); return; }
      const op = await observe(submitted.handle);
      assert.equal(op.state, 'failed', JSON.stringify(op)); assert.equal(op.result?.code ?? op.error?.code, code);
      evidence.checks.push(`${name}: ${code} before native effect`);
    }
    const journal = async () => (await readFile(path.join(path.dirname(connectionFile), 'journal/operations.jsonl'), 'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse);
    try {
      await client.connect(transport);
      const caps = await call('get_capabilities'); evidence.bridge = caps.bridge;
      check('real native source advertises the exact sensing profile and two public tools', () => {
        assert.equal(caps.connected, true); assert.equal(caps.bridge.simulation, true); assert.equal(caps.bridge.hardware_qualified, false);
        assert.equal(caps.bridge.vacuum_sensing.profile, 'native-vacuum-sensing-v1');
        assert.equal(caps.bridge.vacuum_sensing.available, true);
        assert.equal(caps.bridge.vacuum_sensing.source_profile, 'controlled-native-vacuum-v1');
        for (const tool of ['measure_sensor', 'verify_part_state']) assert.ok(caps.tools.includes(`openpnp_${tool}`));
      });
      assert.equal((await client.listTools()).tools.length, 61);
      const initial = await status(); assert.equal(initial.machine.enabled, false); assert.equal(initial.machine.homed, false);
      session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 600 })).session_id;
      const config = await call('get_configuration');
      const nozzle = config.settings.nozzles.find(n => n.vacuum_sensing?.settings_editable);
      assert.ok(nozzle, 'Simulator must expose native editable sensing settings');
      const nativeSettings = nozzle.vacuum_sensing; const nozzleId = nativeSettings.nozzle_id;
      const change = { ...changeFrom(nativeSettings), part_on_low: 61, part_on_high: 79 };
      const plan = await call('plan_configuration', { session_id: session, expected_config_revision: config.config_revision, changes: [change] });
      const applied = await operation('apply_configuration', { plan_id: plan.plan_id });
      const updated = await call('get_configuration');
      const readback = updated.settings.nozzles.find(n => n.vacuum_sensing?.nozzle_id === nozzleId).vacuum_sensing;
      check('typed retained plan applies all requested settings with native readback and no source or occupancy grant', () => {
        assert.notEqual(updated.config_revision, config.config_revision);
        assert.deepEqual(changeFrom(readback), change);
        assert.equal(readback.source_authority_granted, false); assert.equal(readback.measurement_performed, false);
      });
      evidence.configuration_application = applied.operation_id;
      await operation('set_machine_enabled', { enabled: true }); await operation('home_machine');
      const measured = await request('measure_sensor', { nozzle_id: nozzleId, samples: 3 });
      const readings = await observe(measured.handle);
      check('native passive measurements return declared actuator units and no inferred occupancy', () => {
        assert.equal(readings.state, 'succeeded'); assert.deepEqual(readings.result.samples, [70, 70, 70]);
        assert.equal(readings.result.reading_units, 'native-actuator-units'); assert.equal(readings.result.part_state_inferred, false);
        assert.equal(readings.result.simulation_only, true); assert.equal(readings.result.physical_qualification, false);
      });
      const beforeDuplicate = await journal();
      const duplicate = await call('measure_sensor', measured.bound);
      assert.equal(duplicate.operation_id, readings.operation_id);
      assert.equal((await journal()).filter(r => r.type.startsWith('vacuum_')).length, beforeDuplicate.filter(r => r.type.startsWith('vacuum_')).length);
      const requestStatus = await call('get_request_status', { request_id: measured.bound.request_id, view: 'progress' });
      assert.equal(requestStatus.operation.operation_id, readings.operation_id);
      assert.equal(requestStatus.operation.vacuum_sensing_journal.pending_count, 0);
      const on = await operation('verify_part_state', { nozzle_id: nozzleId, state: 'part_on' });
      assert.equal(on.result.native_verdict, true);
      const off = await operation('verify_part_state', { nozzle_id: nozzleId, state: 'part_off' });
      assert.equal(off.result.native_verdict, true);
      const fresh = await status();
      check('actual part-off probe finishes and status preserves fresh journal state', () => {
        const observed = fresh.vacuum_sensing_journal.nozzles.find(n => n.nozzle_id === nozzleId);
        assert.equal(observed.state, 'observed_empty'); assert.equal(observed.sticky_fault, false);
        assert.equal(fresh.vacuum_sensing_journal.pending_count, 0); assert.equal(fresh.vacuum_sensing_journal.execution_authority_restored, false);
      });
      const part = config.parts.find(p => p.id === 'R0603-1K'); assert.ok(part?.height_mm > 0);
      const imported = await call('import_job', { format: 'reference-csv', units: 'mm', widthMm: 30, heightMm: 20, boardId: 'sensing-sdk',
        content: `Ref,Val,Package,PosX,PosY,Rot,Side,PartId,Height\nR1,1K,${part.package_id},7,5,0,top,${part.id},${part.height_mm}\n` });
      const prepared = await operation('prepare_job', { artifact_id: imported.artifact_id });
      const validation = await operation('validate_job'); assert.equal(validation.result.valid, true, JSON.stringify(validation));
      const legacyJobRequest = 'sensing-sdk-job 零  ';
      const job = await operation('start_job', { job_id: prepared.result.job_id, request_id: legacyJobRequest });
      assert.equal(job.request_id, legacyJobRequest, 'Legacy request strings remain exact through MCP admission and native sensing');
      assert.equal(job.result.placed, 1); evidence.completed_native_placements = 1;
      const rows = await journal();
      const sensing = rows.filter(r => ['vacuum_observation_intent', 'vacuum_observation_outcome'].includes(r.type));
      const jobSensing = sensing.filter(r => r.payload.context?.scope === 'job');
      check('native placement checks retain the real job, operation, board-load and material revisions', () => {
        assert.ok(jobSensing.length > 0, 'Expected durable actual native job sensing observations');
        for (const row of jobSensing) {
          const c = row.payload.context; assert.equal(c.operation_id, job.operation_id); assert.equal(c.request_id, job.request_id);
          assert.equal(c.job_context.job_id, prepared.result.job_id); assert.match(c.job_context.job_revision, /^[a-f0-9]{64}$/);
          assert.match(c.job_context.board_load_revision, /^load-/); assert.match(c.job_context.material_setup_revision, /^material-/);
          assert.match(c.job_context.lineage_id, /^[a-f0-9-]{36}$/);
        }
        const checks = jobSensing.filter(r => r.payload.native_event === 'check.returned');
        assert.ok(checks.some(r => r.payload.data.native_stage === 'after_pick' && r.payload.data.verdict === true));
        assert.ok(checks.some(r => r.payload.data.native_stage === 'after_place' && r.payload.data.verdict === true));
      });
      evidence.native_job_sensing_records = jobSensing;
      await operation('set_machine_enabled', { enabled: false });
      const beforeFaultConfig = await call('get_configuration');
      const current = beforeFaultConfig.settings.nozzles.find(n => n.vacuum_sensing?.nozzle_id === nozzleId).vacuum_sensing;
      const faultChange = { ...changeFrom(current), part_off_low: 20, part_off_high: 30 };
      const faultPlan = await call('plan_configuration', { session_id: session, expected_config_revision: beforeFaultConfig.config_revision, changes: [faultChange] });
      await operation('apply_configuration', { plan_id: faultPlan.plan_id });
      await operation('set_machine_enabled', { enabled: true }); await operation('home_machine');
      const fault = await operation('verify_part_state', { nozzle_id: nozzleId, state: 'part_off' });
      assert.equal(fault.result.native_verdict, false);
      const beforeBlocked = await journal();
      await rejected('measure_sensor', { nozzle_id: nozzleId }, 'VACUUM_OUTCOME_UNKNOWN');
      await rejected('verify_part_state', { nozzle_id: nozzleId, state: 'part_off' }, 'VACUUM_OUTCOME_UNKNOWN');
      await rejected('start_job', { job_id: prepared.result.job_id }, 'VACUUM_OUTCOME_UNKNOWN');
      const isSensingObservation = r => ['vacuum_observation_intent', 'vacuum_observation_outcome'].includes(r.type);
      assert.equal((await journal()).filter(isSensingObservation).length, beforeBlocked.filter(isSensingObservation).length);
      const faultStatus = await status();
      check('failed part-off remains retained and blocks later sensing/job effects while status remains readable', () => {
        const observed = faultStatus.vacuum_sensing_journal.nozzles.find(n => n.nozzle_id === nozzleId);
        assert.equal(observed.state, 'retained'); assert.equal(observed.sticky_fault, true);
        assert.ok(faultStatus.vacuum_sensing_journal.sticky_fault_count > 0); assert.equal(faultStatus.vacuum_sensing_journal.pending_count, 0);
      });
      await operation('set_machine_enabled', { enabled: false });
      await call('release_control_session', { session_id: session }); session = undefined;
      const stopped = await status(); assert.equal(stopped.machine.enabled, false);
      assert.equal(stopped.vacuum_sensing_journal.nozzles.find(n => n.nozzle_id === nozzleId).sticky_fault, true);
      const journalBytes = await readFile(path.join(path.dirname(connectionFile), 'journal/operations.jsonl'));
      evidence.journal_sha256 = sha(journalBytes); evidence.final_status = stopped;
      assert.deepEqual(await hashes(), evidence.source_hashes); evidence.passed = true;
    } catch (error) {
      evidence.passed = false; evidence.failure = { code: error.code, message: error.message, stack: error.stack, details: error.details };
      try { evidence.failure_status = await status(); } catch (secondary) { evidence.status_error = { code: secondary.code, message: secondary.message }; }
      throw error;
    } finally {
      if (session) try { await call('release_control_session', { session_id: session }); } catch { /* Retain failure; runner stops its owned simulator. */ }
      await client.close(); evidence.finished_at = new Date().toISOString();
      await writeFile(path.join(output, 'mcp-native-vacuum-sensing.json'), JSON.stringify(evidence,
        (key, value) => key === 'session_id' ? '<redacted>' : key === 'configuration_root' ? '<owned-native-configuration>' : value, 2) + '\n');
    }
  });
