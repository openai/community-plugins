import { DomainError, LIMITS, fail, finite, identifier, normalizeAngle, side, digest, textInput } from './common.mjs';

const identity = [1, 0, 0, 1, 0, 0];
const clean = n => Math.abs(n) < 1e-12 ? 0 : n;
function pose(p, label) {
  if (!p || typeof p !== 'object') fail('invalid_object', `${label} must be an object.`);
  for (const key of ['x', 'y', 'z', 'rotation']) finite(p[key], `${label}.${key}`);
}
function matrix(frame) {
  pose(frame, 'frame'); side(frame.side);
  const angle = frame.rotation * Math.PI / 180, c = Math.cos(angle), s = Math.sin(angle);
  if (frame.side === 'bottom') {
    finite(frame.widthMm, 'frame.widthMm', { min: Number.MIN_VALUE });
    return [-c, -s, -s, c, frame.x + frame.widthMm * c, frame.y + frame.widthMm * s];
  }
  return [c, s, -s, c, frame.x, frame.y];
}
function multiply(a, b) {
  return [a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1], a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3], a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5]];
}
function apply(p, m, z = 0) {
  pose(p, 'placement');
  const sign = m[0]*m[3]-m[1]*m[2] < 0 ? -1 : 1;
  const angle = Math.atan2(sign*m[1], sign*m[0]) * 180 / Math.PI;
  return { x: clean(m[0]*p.x+m[2]*p.y+m[4]), y: clean(m[1]*p.x+m[3]*p.y+m[5]), z: p.z+z,
    rotation: normalizeAngle(angle+p.rotation), units: 'mm' };
}
/** Preview only: OpenPnP top-view XY and side-specific stored placement angles. */
export function transformPlacement(placement, frame) { return apply(placement, matrix(frame), frame.z); }

function list(value, label) {
  if (!Array.isArray(value) || value.length > LIMITS.placements) fail('invalid_array', `${label} must be a bounded array.`);
  return value;
}
function uniqueMap(items, label) {
  const map = new Map();
  for (const item of list(items, label)) {
    if (!item || typeof item !== 'object') fail('invalid_object', `Invalid ${label} entry.`);
    const id = identifier(item.id, `${label}.id`); if (map.has(id)) fail('duplicate_id', `Duplicate ${label} identity ${id}.`); map.set(id, item);
  }
  return map;
}
function bool(value, label) { if (typeof value !== 'boolean') fail('invalid_boolean', `${label} must be boolean.`); }

/** Flatten only active physical side; return disabled/X-out rows visibly with enabled:false. */
export function flattenJob(job) {
  if (!job || job.schemaVersion !== 1 || job.units !== 'mm' || job.coordinateConvention !== 'openpnp-top-view') fail('incompatible_schema', 'Expected canonical job schema 1, mm, openpnp-top-view.');
  identifier(job.id, 'job.id'); const boards = uniqueMap(job.boards, 'boards'), panels = uniqueMap(job.panels, 'panels');
  const output = []; let traversed = 0;
  function walk(instances, ancestors, parentMatrix, parentZ, bottom, active, stack) {
    uniqueMap(instances, 'instances');
    for (const instance of instances) {
      if (++traversed > LIMITS.placements) fail('input_limit', 'Expanded instance count exceeds limit.');
      if (ancestors.length > LIMITS.depth) fail('input_limit', 'Panel nesting exceeds limit.');
      if (!['board', 'panel'].includes(instance.kind)) fail('invalid_instance', 'Instance kind must be board or panel.');
      bool(instance.enabled, 'instance.enabled'); side(instance.side); pose(instance, 'instance');
      const def = (instance.kind === 'board' ? boards : panels).get(instance.definitionId);
      if (!def) fail('missing_definition', `Unknown ${instance.kind} definition ${instance.definitionId}.`);
      const definitionKey = `${instance.kind}:${def.id}`;
      if (stack.includes(definitionKey)) fail('panel_cycle', 'Panel definitions contain a cycle.', { path: [...stack, definitionKey] });
      const path = [...ancestors, instance.id], m = multiply(parentMatrix, matrix({ ...instance, widthMm: def.widthMm }));
      const z = parentZ + instance.z, isBottom = bottom !== (instance.side === 'bottom'), enabled = active && instance.enabled;
      if (instance.kind === 'panel') walk(list(def.children, 'panel.children'), path, m, z, isBottom, enabled, [...stack, definitionKey]);
      else {
        const seen = new Set();
        for (const p of list(def.placements, 'board.placements')) {
          pose(p, 'placement');
          identifier(p.ref, 'placement.ref'); side(p.side); bool(p.enabled, 'placement.enabled'); pose(p, 'placement');
          if (seen.has(p.ref)) fail('duplicate_reference', `Duplicate placement ${p.ref} in board ${def.id}.`); seen.add(p.ref);
          if (p.side !== (isBottom ? 'bottom' : 'top')) continue;
          if (output.length >= LIMITS.placements) fail('input_limit', 'Expanded placement count exceeds limit.');
          output.push({ ...p, enabled: enabled && p.enabled, excludedByInstance: !enabled, boardId: def.id,
            instancePath: path, placementKey: JSON.stringify([...path, p.ref]), machinePose: apply(p, m, z) });
        }
      }
    }
  }
  walk(list(job.instances, 'job.instances'), [], identity, 0, false, true, []);
  return output;
}

export function validateJob(job, options = {}) {
  const { mode = 'offline', feeders, nozzleTips } = options;
  if (!['offline', 'production'].includes(mode)) fail('invalid_mode', 'Validation mode must be offline or production.');
  const issues = [], add = (code, message, details = {}, severity = 'error') => issues.push({ code, message, severity, ...details });
  let placements = [];
  try {
    if (!job || job.schemaVersion !== 1 || job.units !== 'mm' || job.coordinateConvention !== 'openpnp-top-view') fail('incompatible_schema', 'Expected canonical job schema 1, mm, openpnp-top-view.');
    const parts = uniqueMap(job.parts, 'parts');
    for (const part of parts.values()) {
      identifier(part.packageId, 'part.packageId'); if (typeof part.value !== 'string') fail('invalid_part', 'Part value must be text.');
      if (part.heightMm !== null) finite(part.heightMm, 'part.heightMm', { min: Number.MIN_VALUE });
    }
    const boards = uniqueMap(job.boards, 'boards'), panels = uniqueMap(job.panels, 'panels');
    const visited = new Set(); let edges = 0;
    function inspectPanel(id, ancestors = []) {
      if (ancestors.includes(id)) fail('panel_cycle', 'Panel definitions contain a cycle.');
      if (ancestors.length > LIMITS.depth) fail('input_limit', 'Panel nesting exceeds limit.');
      if (visited.has(id)) return; const p = panels.get(id);
      for (const field of ['widthMm', 'heightMm']) if (p[field] !== null) finite(p[field], `panel.${field}`, { min: Number.MIN_VALUE });
      for (const c of uniqueMap(p.children, 'panel.children').values()) {
        if (++edges > LIMITS.placements) fail('input_limit', 'Panel definition edge limit exceeded.');
        pose(c, 'panel.child'); side(c.side); bool(c.enabled, 'panel.child.enabled');
        if (!['board', 'panel'].includes(c.kind)) fail('invalid_instance', 'Instance kind must be board or panel.');
        if (!(c.kind === 'board' ? boards : panels).has(c.definitionId)) fail('missing_definition', `Unknown ${c.kind} definition ${c.definitionId}.`);
        if (c.kind === 'panel') inspectPanel(c.definitionId, [...ancestors, id]);
      }
      visited.add(id);
    }
    for (const id of panels.keys()) inspectPanel(id);
    for (const b of boards.values()) {
      for (const field of ['widthMm', 'heightMm']) {
        if (b[field] === null) add('missing_board_dimension', `Board ${b.id} has unknown ${field}.`, { boardId: b.id }, mode === 'production' ? 'error' : 'warning');
        else finite(b[field], field, { min: Number.MIN_VALUE });
      }
      const refs = new Set();
      for (const p of list(b.placements, 'placements')) {
        pose(p, 'placement'); identifier(p.ref, 'placement.ref');
        if (refs.has(p.ref)) fail('duplicate_reference', `Duplicate placement ${p.ref} in board ${b.id}.`); refs.add(p.ref);
        identifier(p.partId, 'partId'); identifier(p.packageId, 'packageId'); pose(p, 'placement'); side(p.side); bool(p.enabled, 'placement.enabled');
        if (!['placement', 'fiducial'].includes(p.type)) fail('invalid_placement_type', 'Placement type is invalid.');
        const part = parts.get(p.partId);
        if (!part) add('missing_part', `Unknown part ${p.partId}.`, { ref: p.ref });
        else if (part.packageId !== p.packageId || part.heightMm !== p.heightMm) add('part_definition_conflict', `Placement ${p.ref} disagrees with its part definition.`, { ref: p.ref });
        if (p.heightMm !== null) finite(p.heightMm, 'part.heightMm', { min: Number.MIN_VALUE });
        if (p.x < 0 || p.y < 0 || (b.widthMm !== null && p.x > b.widthMm) || (b.heightMm !== null && p.y > b.heightMm))
          add('placement_outside_board', `Placement ${p.ref} lies outside the board's declared rectangle.`, { ref: p.ref, boardId: b.id });
      }
    }
    placements = flattenJob(job);
    const active = placements.filter(p => p.enabled && p.type === 'placement');
    if (!active.length) add('no_enabled_placements', 'No enabled placements on the selected physical side.');
    for (const p of active) if (p.heightMm === null) add('missing_part_height', `Part height is unknown for ${p.ref}.`, { placementKey: p.placementKey }, mode === 'production' ? 'error' : 'warning');
    if (mode === 'production') {
      if (!Array.isArray(feeders)) add('missing_feeder_inventory', 'Production data validation requires feeder inventory.');
      if (!Array.isArray(nozzleTips)) add('missing_tip_inventory', 'Production data validation requires nozzle-tip inventory.');
      if (Array.isArray(feeders)) {
        uniqueMap(feeders, 'feeders'); const needed = new Map(); for (const p of active) needed.set(p.partId, (needed.get(p.partId) ?? 0) + 1);
        for (const [partId, count] of needed) {
          const matches = feeders.filter(f => f.enabled === true && f.partId === partId);
          if (!matches.length) add('missing_feeder', `No enabled feeder for ${partId}.`, { partId });
          else if (matches.some(f => !Number.isSafeInteger(f.remaining) || f.remaining < 0)) add('unknown_material_count', `Feeder count is unknown/invalid for ${partId}.`, { partId });
          else if (matches.reduce((n, f) => n + f.remaining, 0) < count) add('insufficient_material', `Insufficient material for ${partId}.`, { partId, required: count });
        }
      }
      if (Array.isArray(nozzleTips)) { uniqueMap(nozzleTips, 'nozzleTips'); for (const p of active) if (!nozzleTips.some(t => t.enabled === true && Array.isArray(t.packageIds) && t.packageIds.includes(p.packageId))) add('incompatible_nozzle_tip', `No enabled compatible tip for ${p.packageId}.`, { placementKey: p.placementKey }); }
    }
  } catch (e) { if (!(e instanceof DomainError)) throw e; add(e.code, e.message, e.details); }
  return { valid: !issues.some(i => i.severity === 'error'), mode, productionQualified: false, issues, placements,
    counts: { total: placements.length, enabled: placements.filter(p => p.enabled && p.type === 'placement').length,
      excluded: placements.filter(p => !p.enabled).length, fiducials: placements.filter(p => p.type === 'fiducial').length } };
}

export function importCanonicalJob(content) {
  const source = textInput(content); let job;
  try { job = JSON.parse(source); } catch { fail('invalid_json', 'Canonical job must be JSON.'); }
  // Hashing enforces acyclic plain JSON, finite values and bounded object depth.
  digest(job); const result = validateJob(job);
  if (!result.valid) fail('invalid_job', 'Canonical job failed validation.', { diagnostics: result.issues });
  const { revision: _old, ...body } = job; return { ...body, revision: digest(body) };
}
export function exportCanonicalJob(job) {
  const result = validateJob(job); if (!result.valid) fail('invalid_job', 'Cannot export invalid canonical job.', { diagnostics: result.issues });
  return JSON.stringify(job, null, 2) + '\n';
}
