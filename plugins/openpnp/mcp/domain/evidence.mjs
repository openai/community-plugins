import { LIMITS, fail, finite, identifier, digest, normalizeAngle } from './common.mjs';
import { flattenJob } from './geometry.mjs';

function oneOf(value, values, name) { if (!values.includes(value)) fail('invalid_inspection', `${name} must be one of ${values.join(', ')}.`); }
function revision(value, name) { if (typeof value !== 'string' || !/^[a-f0-9]{64}$/u.test(value)) fail('invalid_revision', `${name} must be a SHA-256 hex digest.`); return value; }
function timestamp(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{3})?Z$/u.test(value) || !Number.isFinite(Date.parse(value)) || new Date(value).toISOString().replace('.000Z', 'Z') !== value.replace('.000Z', 'Z'))
    fail('invalid_timestamp', 'observedAt must be a valid UTC ISO-8601 timestamp.');
  return value;
}

/** Construct a deterministic, quantitative inspection record; no machine authority is granted. */
export function recordInspection(input) {
  if (!input || typeof input !== 'object') fail('invalid_inspection', 'Inspection input must be an object.');
  const { jobRevision, configurationRevision, boardLoadId, placementKey, method, actor, observedAt, presence, polarity, measurements, tolerances, evidence } = input;
  revision(jobRevision, 'jobRevision'); revision(configurationRevision, 'configurationRevision');
  identifier(boardLoadId, 'boardLoadId'); identifier(placementKey, 'placementKey'); identifier(actor, 'actor'); timestamp(observedAt);
  oneOf(method, ['operator-metrology', 'calibrated-vision', 'external-metrology'], 'method');
  oneOf(presence, ['present', 'missing', 'unknown'], 'presence');
  oneOf(polarity, ['correct', 'incorrect', 'not-applicable', 'unknown'], 'polarity');
  if (!tolerances || !measurements) fail('invalid_inspection', 'Explicit tolerances and measurements are required.');
  for (const key of ['xyMm', 'rotationDeg']) finite(tolerances[key], `tolerances.${key}`, { min: Number.MIN_VALUE });
  for (const key of ['dxMm', 'dyMm', 'rotationErrorDeg']) finite(measurements[key], `measurements.${key}`);
  for (const key of ['uncertaintyMm', 'uncertaintyDeg']) finite(measurements[key], `measurements.${key}`, { min: 0 });
  if (!Array.isArray(evidence) || !evidence.length || evidence.length > 32) fail('missing_evidence', 'Supply 1–32 immutable evidence references.');
  const references = evidence.map(e => {
    if (!e || typeof e !== 'object') fail('missing_evidence', 'Evidence reference must be an object.');
    identifier(e.id, 'evidence.id'); revision(e.sha256, 'evidence.sha256');
    oneOf(e.kind, ['image', 'measurement-file', 'operator-note'], 'evidence.kind');
    return { id: e.id, sha256: e.sha256, kind: e.kind };
  });
  const error = { xyMm: Math.hypot(measurements.dxMm, measurements.dyMm), rotationDeg: Math.abs(normalizeAngle(measurements.rotationErrorDeg)) };
  const definitelyOutside = error.xyMm - measurements.uncertaintyMm > tolerances.xyMm || error.rotationDeg - measurements.uncertaintyDeg > tolerances.rotationDeg;
  const definitelyInside = error.xyMm + measurements.uncertaintyMm <= tolerances.xyMm && error.rotationDeg + measurements.uncertaintyDeg <= tolerances.rotationDeg;
  const status = presence === 'missing' || polarity === 'incorrect' || definitelyOutside ? 'fail'
    : presence === 'present' && polarity !== 'unknown' && definitelyInside ? 'pass' : 'uncertain';
  const body = { schemaVersion: 1, jobRevision, configurationRevision, boardLoadId, placementKey, method, actor, observedAt,
    presence, polarity, measurements: { dxMm: measurements.dxMm, dyMm: measurements.dyMm, rotationErrorDeg: measurements.rotationErrorDeg, uncertaintyMm: measurements.uncertaintyMm, uncertaintyDeg: measurements.uncertaintyDeg },
    tolerances: { xyMm: tolerances.xyMm, rotationDeg: tolerances.rotationDeg }, evidence: references, error, status };
  return { ...body, id: digest(body) };
}

export function verifyInspection(record) {
  if (!record || typeof record !== 'object') fail('invalid_inspection', 'Inspection record must be an object.');
  const canonical = recordInspection(record);
  const { id, ...body } = record;
  if (id !== canonical.id || digest(body) !== id) fail('inspection_tampered', 'Inspection content or status differs from its immutable receipt.');
  return canonical;
}

/** First-article evidence coverage; hardware commissioning remains a separate native gate. */
export function assessInspectionCoverage({ job, configurationRevision, boardLoadId, inspections }) {
  if (!job || typeof job !== 'object') fail('invalid_job', 'Canonical job is required.');
  revision(job.revision, 'job.revision'); revision(configurationRevision, 'configurationRevision'); identifier(boardLoadId, 'boardLoadId');
  if (!Array.isArray(inspections) || inspections.length > LIMITS.placements) fail('invalid_inspection', 'Inspections must be a bounded array.');
  const { revision: _revision, ...body } = job;
  if (digest(body) !== job.revision) fail('revision_mismatch', 'Job content changed since its revision was issued.');
  const required = flattenJob(job).filter(p => p.enabled && p.type === 'placement');
  const expected = new Set(required.map(p => p.placementKey)), current = new Map(), issues = [];
  for (const record of inspections) {
    const r = verifyInspection(record);
    if (r.jobRevision !== job.revision || r.configurationRevision !== configurationRevision || r.boardLoadId !== boardLoadId)
      fail('inspection_scope_mismatch', 'Inspection belongs to a different job, configuration or physical board load.', { inspectionId: r.id });
    if (!expected.has(r.placementKey)) fail('unknown_placement', 'Inspection does not identify an enabled placement in this load.', { placementKey: r.placementKey });
    const existing = current.get(r.placementKey);
    if (existing && Date.parse(existing.observedAt) === Date.parse(r.observedAt) && existing.id !== r.id)
      fail('ambiguous_inspection', 'Conflicting inspections share the same timestamp.', { placementKey: r.placementKey });
    if (!existing || Date.parse(r.observedAt) > Date.parse(existing.observedAt)) current.set(r.placementKey, r);
  }
  for (const p of required) {
    const r = current.get(p.placementKey);
    if (!r || r.status !== 'pass') issues.push({ code: r ? `inspection_${r.status}` : 'inspection_missing', placementKey: p.placementKey, inspectionId: r?.id ?? null });
  }
  if (!required.length) issues.push({ code: 'no_enabled_placements' });
  return { inspectionComplete: issues.length === 0, productionQualified: false, jobRevision: job.revision, configurationRevision, boardLoadId,
    required: required.length, inspected: current.size, passed: [...current.values()].filter(r => r.status === 'pass').length, issues };
}
