import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareJob, flattenJob, transformPlacement, validateJob, digest, DomainError } from '../../plugins/openpnp/mcp/domain/index.mjs';

const placement = { x: 7, y: 4, z: 0.2, rotation: 135 };
const frame = { x: 100, y: 200, z: 2, rotation: 90, side: 'top', widthMm: 40 };
function board() {
  return prepareJob({ format: 'reference-csv', content: 'Ref,Value,Package,X,Y,Rotation,Side,Height\nR1,10k,R_0603,7,4,135,top,0.5\nC1,1u,C_0603,7,4,135,bottom,0.8\n', units: 'mm', widthMm: 40, heightMm: 30 });
}
function panelJob() {
  const j = board();
  j.panels = [{ id: 'panel-1', widthMm: 100, heightMm: 60, children: [
    { id: 'a', kind: 'board', definitionId: 'board-1', x: 0, y: 0, z: 0, rotation: 0, side: 'top', enabled: true },
    { id: 'b', kind: 'board', definitionId: 'board-1', x: 50, y: 0, z: 0, rotation: 0, side: 'top', enabled: false },
  ] }];
  j.instances = [{ id: 'panel-load', kind: 'panel', definitionId: 'panel-1', x: 100, y: 200, z: 1, rotation: 90, side: 'top', enabled: true }];
  const { revision: _, ...body } = j; j.revision = digest(body); return j;
}
test('board top/bottom coordinate transforms follow pinned OpenPnP placement-angle convention', () => {
  assert.deepEqual(transformPlacement(placement, frame), { x: 96, y: 207, z: 2.2, rotation: -135, units: 'mm' });
  assert.deepEqual(transformPlacement(placement, { ...frame, side: 'bottom' }), { x: 96, y: 233, z: 2.2, rotation: -135, units: 'mm' });
  assert.throws(() => transformPlacement(placement, { ...frame, side: 'bottom', widthMm: null }), e => e instanceof DomainError && e.code === 'invalid_number');
});

test('panel instance X-outs retain visible excluded records without changing shared board', () => {
  const j = panelJob(), out = flattenJob(j);
  assert.deepEqual(out.map(p => [p.placementKey, p.enabled, p.machinePose.x, p.machinePose.y]), [
    ['["panel-load","a","R1"]', true, 96, 207], ['["panel-load","b","R1"]', false, 96, 257],
  ]);
  assert.equal(j.boards[0].placements[0].enabled, true);
  assert.equal(validateJob(j).counts.excluded, 1);
});

test('nested rotations and side parity compose once at each frame', () => {
  const j = panelJob();
  j.panels[0].children = [{ id: 'a', kind: 'board', definitionId: 'board-1', x: 50, y: 0, z: 0, rotation: 90, side: 'bottom', enabled: true }];
  j.instances[0].side = 'bottom'; j.instances[0].rotation = 0;
  // Inner bottom/90: (7,4) -> (46,33); outer bottom: (46,33) -> (154,233).
  // Two reflections restore top side. The resulting matrix is a -90 degree rotation.
  const [p] = flattenJob(j);
  assert.equal(p.ref, 'R1'); assert.deepEqual(p.machinePose, { x: 154, y: 233, z: 1, rotation: 45, units: 'mm' });
});

test('cycles, missing definitions, duplicate IDs, invalid unused boards and invalid poses fail', () => {
  let j = panelJob(); j.panels[0].children.push({ ...j.instances[0], id: 'cycle' });
  assert.equal(validateJob(j).issues[0].code, 'panel_cycle');
  j = panelJob(); j.panels[0].children[0].definitionId = 'absent';
  assert.equal(validateJob(j).issues[0].code, 'missing_definition');
  j = panelJob(); j.panels[0].children[1].id = 'a';
  assert.equal(validateJob(j).issues[0].code, 'duplicate_id');
  j = board(); j.boards.push({ ...structuredClone(j.boards[0]), id: 'unused' }); j.boards[1].placements[0].x = Infinity;
  assert.equal(validateJob(j).valid, false);
  j = board(); j.boards[0].placements[0] = null;
  assert.equal(validateJob(j).issues[0].code, 'invalid_object');
});

test('production data checks heights, capacity, compatible tips and board bounds without granting authority', () => {
  const j = panelJob(), activePart = j.boards[0].placements[0].partId;
  const inventory = { mode: 'production', feeders: [{ id: 'f1', partId: activePart, enabled: true, remaining: 1 }], nozzleTips: [{ id: 't1', enabled: true, packageIds: ['R_0603'] }] };
  const valid = validateJob(j, inventory);
  assert.equal(valid.valid, true); assert.equal(valid.productionQualified, false);
  assert.equal(validateJob(j, { ...inventory, feeders: [] }).issues[0].code, 'missing_feeder');
  assert.equal(validateJob(j, { ...inventory, feeders: [{ ...inventory.feeders[0], remaining: 0 }] }).issues[0].code, 'insufficient_material');
  assert.equal(validateJob(j, { ...inventory, nozzleTips: [] }).issues[0].code, 'incompatible_nozzle_tip');
  j.boards[0].placements[0].x = 41;
  assert.equal(validateJob(j).issues[0].code, 'placement_outside_board');
});
