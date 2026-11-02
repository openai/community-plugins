import assert from 'node:assert/strict';
import { readFileSync, realpathSync } from 'node:fs';
import { isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { TOOL_DEFINITIONS } from '../../src/openpnp/node/contracts.mjs';

const root = fileURLToPath(new URL('../../plugins/openpnp/', import.meta.url));
const evaluationRoot = resolve(root, 'assets/evaluations');
const catalog = JSON.parse(readFileSync(resolve(evaluationRoot, 'catalog.json'), 'utf8'));
const skills = JSON.parse(readFileSync(resolve(root, 'references/skill-contracts.json'), 'utf8')).skills;
const versioned = JSON.parse(readFileSync(resolve(evaluationRoot, 'job-edit-tool-contracts.json'), 'utf8'));

function readFixture(path) {
  const full = realpathSync(resolve(evaluationRoot, path));
  const inside = relative(realpathSync(evaluationRoot), full);
  assert.ok(inside && inside !== '..' && !inside.startsWith(`..${sep}`) && !isAbsolute(inside));
  return JSON.parse(readFileSync(full, 'utf8'));
}

test('each shipped skill has independently deliverable raw cases and separate assessor rubrics', () => {
  assert.equal(catalog.schema_version, 1);
  assert.equal(catalog.qualification_status, 'fixtures_authored_not_executed');
  assert.equal(catalog.cases.length, skills.length * 3);
  assert.equal(new Set(catalog.cases.map(row => row.case_id)).size, catalog.cases.length);
  for (const skill of skills) {
    const rows = catalog.cases.filter(row => row.skill === skill.name);
    assert.deepEqual(rows.map(row => row.scenario_kind).sort(), ['failure', 'happy', 'recovery']);
    for (const row of rows) {
      assert.notEqual(row.case, row.rubric, 'Raw artifacts and grading targets must be separable');
      const fixture = readFixture(row.case);
      const rubric = readFixture(row.rubric);
      assert.equal(fixture.case_id, row.case_id);
      assert.equal(rubric.case_id, row.case_id);
      assert.equal(fixture.skill, skill.name);
      assert.equal(rubric.skill, skill.name);
      assert.ok(fixture.request.length > 20);
      assert.ok(Object.keys(fixture.observations).length >= 3);
      assert.ok(fixture.execution_boundary.length > 0);
      // Keep the raw historical cases byte-identical. Each skill declares exact
      // later additions rather than modifying inputs or claiming broader competence.
      const additions = versioned.per_skill_additions[skill.name];
      assert.deepEqual(skill.post_historical_tool_additions, additions);
      assert.equal(new Set(additions).size, additions.length);
      for (const name of additions) {
        assert.ok(skill.tool_names.includes(name));
        assert.ok(!fixture.tool_inventory.candidate_tools.includes(name));
      }
      assert.equal(createHash('sha256').update(readFileSync(resolve(evaluationRoot, row.case))).digest('hex'), versioned.historical_case_sha256[row.case_id]);
      assert.deepEqual(fixture.tool_inventory.candidate_tools,
        skill.tool_names.filter(name => !additions.includes(name)));
      for (const assessorField of ['scenario_kind', 'required_outcomes', 'forbidden_decisions', 'grading', 'expected_answer', 'rubric']) {
        assert.ok(!Object.hasOwn(fixture, assessorField), `Assessor answer leaked into raw fixture: ${row.case_id}`);
      }
      assert.equal(rubric.scenario_kind, row.scenario_kind);
      assert.ok(rubric.required_outcomes.length > 0);
      assert.ok(rubric.forbidden_decisions.length > 0);
    }
  }
});

test('the 61-tool source delta stays separate from the frozen 43-tool package and 42 historical skill cases', () => {
  assert.equal(versioned.evidence_class, 'authored-contract-fixture');
  assert.equal(versioned.behavioral_qualification, false); assert.equal(versioned.native_qualification, false); assert.equal(versioned.hardware_qualification, false);
  assert.equal(versioned.historical_case_count, 42); assert.equal(Object.keys(versioned.historical_case_sha256).length, 42);
  const baseline = versioned.frozen_packaged_baseline;
  assert.equal(baseline.tool_count, 43); assert.equal(new Set(baseline.tool_names).size, 43);
  assert.equal(versioned.candidate_source.tool_count, 61);
  assert.deepEqual([...versioned.candidate_source.added_tools].sort(), ['openpnp_get_board_loads', 'openpnp_register_board_load', 'openpnp_inspect_job', 'openpnp_plan_placement_edits', 'openpnp_apply_placement_edits', 'openpnp_export_portable_configuration', 'openpnp_step_job', 'openpnp_plan_placement_structure', 'openpnp_apply_placement_structure', 'openpnp_run_controller_diagnostic', 'openpnp_get_material_loads', 'openpnp_register_material_load', 'openpnp_request_board_inspection', 'openpnp_get_board_inspection', 'openpnp_measure_sensor', 'openpnp_verify_part_state', 'openpnp_request_sensing_reconciliation', 'openpnp_get_sensing_reconciliation'].sort());
  assert.deepEqual(TOOL_DEFINITIONS.map(tool => tool.name).sort(), [...baseline.tool_names, ...versioned.candidate_source.added_tools].sort());
  const packaged = versioned.candidate_packaged_mcp;
  assert.equal(versioned.qualified_expanded50_baseline.tool_count, 50);
  assert.equal(versioned.qualified_expanded50_baseline.native_publication, 'matching-native-build21-published');
  assert.equal(typeof packaged.native_workflow_qualification, 'boolean');
  assert.equal(versioned.historical_material55_candidate.controller_diagnostic_workflow_qualification, true);
  assert.equal(versioned.historical_material55_candidate.material_changeover_workflow_qualification, true);
  assert.equal(versioned.historical_material55_candidate.native_publication, 'private-material55-build01-packaged');
  const historical = versioned.historical_sensing78_before_s79;
  assert.equal(historical.candidate_source.tool_count, 59);
  assert.equal(historical.candidate_packaged_mcp.tool_count, 59);
  assert.equal(historical.candidate_packaged_mcp.native_publication, 'local-sensing78-build03-packaged');
  assert.equal(historical.candidate_packaged_mcp.controller_diagnostic_workflow_qualification, true);
  assert.equal(historical.candidate_packaged_mcp.material_changeover_workflow_qualification, true);
  assert.equal(packaged.historical_receipts_apply_to_this_build, false);
  assert.equal(packaged.qualification_status, 'assembled-unqualified');
  for (const key of ['behavioral_qualification', 'native_workflow_qualification', 'hardware_qualification', 'full_implementation_plan_completed', 'uniform_fresh_skill_qualification', 'sensing_recovery_workflow_qualification'])
    assert.equal(packaged[key], false, 'Historical evidence cannot qualify a newly assembled build: ' + key);
  assert.notEqual(packaged.native_bridge_sha256, historical.candidate_packaged_mcp.native_bridge_sha256);
  assert.equal(new Set(versioned.candidate_source.added_tools).size, 18);
  assert.equal(versioned.qualified_completion52_baseline.native_workflow_qualification, true);
  assert.equal(packaged.tool_count, 61); assert.equal(packaged.dependency_count, 8);
  for (const [file, expected] of [['mcp/server.mjs', packaged.server_sha256], ['mcp/tool-inputs.json', packaged.tool_inputs_sha256], ['THIRD_PARTY_NOTICES.md', packaged.third_party_notices_sha256]])
    assert.equal(createHash('sha256').update(readFileSync(resolve(root, file))).digest('hex'), expected, 'Package metadata must identify the bytes actually supplied.');
  assert.equal(versioned.qualified_expanded52_baseline.native_publication, 'matching-native-build25-published');
  assert.equal(versioned.historical_combined57_candidate.native_publication, 'private-combined57-native-build04-packaged');
  assert.equal(createHash('sha256').update(readFileSync(resolve(root, 'bridge/openpnp-codex-bridge.jar'))).digest('hex'), packaged.native_bridge_sha256);
  assert.deepEqual(JSON.parse(readFileSync(resolve(root, 'mcp/tool-inputs.json'), 'utf8')).tools.map(tool => tool.name).sort(), TOOL_DEFINITIONS.map(tool => tool.name).sort());
  const allowedAdditions = new Set(['openpnp_read_response_page', ...versioned.candidate_source.added_tools]);
  for (const additions of Object.values(versioned.per_skill_additions)) for (const name of additions) assert.ok(allowedAdditions.has(name));
});

test('validate skill routes native board inspection separately from unchanged historical offline cases',()=>{
 const skill=skills.find(x=>x.name==='validate-openpnp-job');
 assert.ok(skill.references.includes('references/native-loaded-board-inspection.md'));
 for(const name of ['openpnp_request_board_inspection','openpnp_get_board_inspection']){
  assert.ok(skill.tool_names.includes(name));assert.ok(skill.post_historical_tool_additions.includes(name));
  assert.ok(versioned.per_skill_additions[skill.name].includes(name));
 }
 const reference=readFileSync(resolve(root,'references/native-loaded-board-inspection.md'),'utf8');
 assert.match(reference,/native-loaded-board-inspection-v1/);assert.match(reference,/submitted only|no MCP tool for submitting/);
 assert.match(reference,/without restoring a form, callback, lease or submission authority/);
 for(const row of catalog.cases.filter(x=>x.skill===skill.name)){
  const raw=readFixture(row.case);assert.ok(!raw.tool_inventory.candidate_tools.includes('openpnp_request_board_inspection'));assert.ok(!raw.tool_inventory.candidate_tools.includes('openpnp_get_board_inspection'));
 }
});

test('numerical evaluation inputs encode the distinctions assessors must judge', () => {
  const better = readFixture('cases/optimize-openpnp-production-01.json').observations;
  assert.equal(better.baseline.placements, better.candidate.placements);
  assert.equal(better.baseline.inspected, better.candidate.inspected);
  assert.ok(better.candidate.duration_s < better.baseline.duration_s);
  const worse = readFixture('cases/optimize-openpnp-production-02.json').observations;
  assert.ok(worse.candidate.duration_s < worse.baseline.duration_s);
  assert.ok(worse.candidate.defects > worse.acceptance_max_defects);
  const vacuum = readFixture('cases/configure-openpnp-tooling-02.json').observations;
  assert.ok(Math.max(...vacuum.part_off_samples) >= Math.min(...vacuum.part_on_samples));
  assert.ok(Math.max(...vacuum.part_on_samples) >= Math.min(...vacuum.part_off_samples));
  const firstArticle = readFixture('cases/validate-openpnp-job-01.json').observations;
  assert.equal(firstArticle.sample_count, firstArticle.placements.length);
  for (const placement of firstArticle.placements) {
    assert.ok(Math.abs(placement.error_x_mm) <= firstArticle.limits.xy_mm);
    assert.ok(Math.abs(placement.error_y_mm) <= firstArticle.limits.xy_mm);
    assert.ok(Math.abs(placement.error_deg) <= firstArticle.limits.rotation_deg);
  }
  // These checks validate raw inputs. They intentionally make no assertion that a model handled the cases correctly.
});
